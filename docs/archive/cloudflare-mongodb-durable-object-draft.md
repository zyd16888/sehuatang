下面给你一份**可直接落地的生产级方案**：在 Cloudflare Workers 上通过 **Durable Objects 复用 MongoDB 连接**，数据继续留在现有 MongoDB Atlas（或其他外部 MongoDB），完全不用迁移。

---

### 1. 项目结构

```
your-api/
├── src/
│   ├── index.ts              # 入口 Worker（路由）
│   ├── mongo-do.ts           # Durable Object：负责保持 Mongo 连接
│   └── types.ts              # 类型定义
├── package.json
├── wrangler.toml
└── tsconfig.json
```

---

### 2. 安装依赖

```bash
npm init -y
npm install mongodb
npm install -D wrangler typescript @cloudflare/workers-types
```

`package.json` 关键字段建议：

```json
{
  "type": "module",
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  }
}
```

---

### 3. `wrangler.toml`

```toml
name = "mongo-api"
main = "src/index.ts"
compatibility_date = "2025-05-01"
compatibility_flags = ["nodejs_compat"]

[vars]
# 可选：默认数据库名
MONGODB_DB = "your_database_name"

[[durable_objects.bindings]]
name = "MONGO_DO"
class_name = "MongoDurableObject"

[[migrations]]
tag = "v1"
new_classes = ["MongoDurableObject"]
```

连接串用 Secret 存（不要写在代码或 toml 里）：

```bash
wrangler secret put MONGODB_URI
# 粘贴：mongodb+srv://user:pass@cluster.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

---

### 4. `src/types.ts`

```ts
export interface Env {
  MONGODB_URI: string;
  MONGODB_DB: string;
  MONGO_DO: DurableObjectNamespace;
}
```

---

### 5. `src/mongo-do.ts`（核心：连接复用）

```ts
import { DurableObject } from "cloudflare:workers";
import { MongoClient, Db, Collection, Document } from "mongodb";
import type { Env } from "./types";

export class MongoDurableObject extends DurableObject<Env> {
  private client: MongoClient | null = null;
  private db: Db | null = null;
  private connecting: Promise<void> | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
  }

  /** 确保已建立连接（懒加载 + 单例） */
  private async ensureConnected(): Promise<Db> {
    if (this.db) return this.db;

    // 防止并发请求同时建连
    if (this.connecting) {
      await this.connecting;
      return this.db!;
    }

    this.connecting = (async () => {
      try {
        this.client = new MongoClient(this.env.MONGODB_URI, {
          maxPoolSize: 1,                 // Workers + DO 环境下必须小
          minPoolSize: 0,
          maxIdleTimeMS: 60_000,          // 空闲 60s 可关闭
          serverSelectionTimeoutMS: 8_000,
          connectTimeoutMS: 10_000,
          socketTimeoutMS: 45_000,
        });

        await this.client.connect();
        this.db = this.client.db(this.env.MONGODB_DB);
        console.log("[MongoDO] connected");
      } catch (err) {
        this.client = null;
        this.db = null;
        throw err;
      } finally {
        this.connecting = null;
      }
    })();

    await this.connecting;
    return this.db!;
  }

  /** 通用执行入口：所有 Mongo 操作都走这里 */
  async exec<T>(
    collectionName: string,
    operation: (coll: Collection<Document>) => Promise<T>
  ): Promise<T> {
    const db = await this.ensureConnected();
    const coll = db.collection(collectionName);
    return operation(coll);
  }

  // ---------- 常用封装（可按需扩展） ----------

  async findOne(collection: string, filter: Document, options?: any) {
    return this.exec(collection, (c) => c.findOne(filter, options));
  }

  async find(
    collection: string,
    filter: Document = {},
    options: { limit?: number; skip?: number; sort?: any; projection?: any } = {}
  ) {
    return this.exec(collection, async (c) => {
      let cursor = c.find(filter, { projection: options.projection });
      if (options.sort) cursor = cursor.sort(options.sort);
      if (options.skip) cursor = cursor.skip(options.skip);
      if (options.limit) cursor = cursor.limit(options.limit);
      return cursor.toArray();
    });
  }

  async insertOne(collection: string, doc: Document) {
    return this.exec(collection, (c) => c.insertOne(doc));
  }

  async insertMany(collection: string, docs: Document[]) {
    return this.exec(collection, (c) => c.insertMany(docs));
  }

  async updateOne(
    collection: string,
    filter: Document,
    update: Document,
    options?: any
  ) {
    return this.exec(collection, (c) => c.updateOne(filter, update, options));
  }

  async updateMany(
    collection: string,
    filter: Document,
    update: Document,
    options?: any
  ) {
    return this.exec(collection, (c) => c.updateMany(filter, update, options));
  }

  async deleteOne(collection: string, filter: Document) {
    return this.exec(collection, (c) => c.deleteOne(filter));
  }

  async deleteMany(collection: string, filter: Document) {
    return this.exec(collection, (c) => c.deleteMany(filter));
  }

  async aggregate(collection: string, pipeline: Document[]) {
    return this.exec(collection, (c) => c.aggregate(pipeline).toArray());
  }

  async countDocuments(collection: string, filter: Document = {}) {
    return this.exec(collection, (c) => c.countDocuments(filter));
  }

  /** 健康检查 */
  async ping() {
    const db = await this.ensureConnected();
    return db.command({ ping: 1 });
  }
}
```

---

### 6. `src/index.ts`（API 入口）

```ts
import { MongoDurableObject } from "./mongo-do";
import type { Env } from "./types";

export { MongoDurableObject };

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // 固定用同一个 DO 实例（全局共享连接）
    // 数据量很大时也可以按业务分片，见文末说明
    const id = env.MONGO_DO.idFromName("global-mongo");
    const stub = env.MONGO_DO.get(id);

    try {
      // ---------- 健康检查 ----------
      if (path === "/health") {
        const pong = await stub.ping();
        return Response.json({ ok: true, mongo: pong });
      }

      // ---------- 示例：查询用户 ----------
      if (path === "/users" && request.method === "GET") {
        const limit = Number(url.searchParams.get("limit") || 20);
        const skip = Number(url.searchParams.get("skip") || 0);
        const status = url.searchParams.get("status");

        const filter: any = {};
        if (status) filter.status = status;

        const users = await stub.find("users", filter, {
          limit,
          skip,
          sort: { createdAt: -1 },
          projection: { password: 0 }, // 排除敏感字段
        });

        return Response.json({ data: users });
      }

      // ---------- 示例：创建用户 ----------
      if (path === "/users" && request.method === "POST") {
        const body = await request.json() as any;
        const result = await stub.insertOne("users", {
          ...body,
          createdAt: new Date(),
          updatedAt: new Date(),
        });
        return Response.json({ insertedId: result.insertedId }, { status: 201 });
      }

      // ---------- 示例：按 ID 查询 ----------
      if (path.startsWith("/users/") && request.method === "GET") {
        const id = path.split("/")[2];
        const user = await stub.findOne("users", { _id: id }); // 注意：如果是 ObjectId 需要转换
        if (!user) return new Response("Not Found", { status: 404 });
        return Response.json(user);
      }

      // ---------- 示例：聚合 ----------
      if (path === "/stats/orders" && request.method === "GET") {
        const pipeline = [
          { $match: { status: "paid" } },
          {
            $group: {
              _id: "$category",
              total: { $sum: "$amount" },
              count: { $sum: 1 },
            },
          },
          { $sort: { total: -1 } },
        ];
        const stats = await stub.aggregate("orders", pipeline);
        return Response.json({ data: stats });
      }

      return new Response("Not Found", { status: 404 });
    } catch (err: any) {
      console.error("[API Error]", err);
      return Response.json(
        { error: err.message || "Internal Server Error" },
        { status: 500 }
      );
    }
  },
};
```

---

### 7. 本地开发与部署

```bash
# 本地开发（会真实连你的 Atlas）
npm run dev

# 部署
npm run deploy
```

部署后访问：

- `https://your-worker.xxx.workers.dev/health`
- `https://your-worker.xxx.workers.dev/users?limit=10`

---

### 8. 数据量大时的优化建议

1. **分片 Durable Object（推荐）**
   不要只用一个全局 DO。可以按业务维度拆：
   ```ts
   // 例如按用户 ID 哈希
   const shardKey = `user-${userId % 16}`; // 16 个分片
   const id = env.MONGO_DO.idFromName(shardKey);
   ```
   或者按集合 / 按租户拆，把压力分散，同时绕过单个 DO 的并发限制。

2. **投影 + 分页必须做**
   数据多时绝对不要 `find({})` 全表，务必带 `projection`、`limit`、`skip`（或更好的 cursor 分页）。

3. **读多写少的场景**
   可以把热点查询结果缓存到 Cloudflare KV 或 Cache API，进一步降低对 Mongo 的压力。

4. **连接串参数**
   Atlas 建议加上 `retryWrites=true&w=majority`，并确认 IP Access List 允许 Cloudflare 的出口（或者用 `0.0.0.0/0` 测试，生产建议更严格）。

5. **ObjectId 处理**
   如果 `_id` 是 ObjectId，前端传字符串时要转换：
   ```ts
   import { ObjectId } from "mongodb";
   const _id = new ObjectId(idStr);
   ```

---

### 总结

- 数据完全不用迁移，继续用现有 MongoDB Atlas。
- 通过 Durable Object 把连接保持在热状态，延迟从几百毫秒降到几十毫秒级别。
- 代码结构清晰，后续要加 `update`、`delete`、事务、更多集合都很容易扩展。
