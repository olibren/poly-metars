// A read-only file server. There is deliberately no collector or D1 binding.
// Cache API applies only to /data, preserving free asset-first website hosting.
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const key = url.pathname.replace(/^\/data\//, "");
    const allowed = /^(index|health)\.json$/.test(key)
      || key === "rejected.jsonl"
      || /^revisions\/[a-f0-9]{64}\/(day\.json|day\.csv|audit\.json|bundle\.zip)$/.test(key)
      || /^evidence\/[a-f0-9]{64}\.txt$/.test(key)
      || /^(receipts|reports|rejected)\/[a-f0-9]{64}\.json$/.test(key);
    if (!url.pathname.startsWith("/data/") || !allowed) {
      return new Response("Not found\n", { status: 404 });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Read only\n", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    // Queries and client Cache-Control headers cannot create new R2 cache keys.
    url.search = "";
    const cacheKey = new Request(url, { method: "GET" });
    const cache = caches.default;
    let response = await cache.match(cacheKey);
    if (!response) {
      const object = await env.ARCHIVE.get(key);
      if (!object) return new Response("Data not published yet\n", { status: 404, headers: { "Cache-Control": "no-store" } });
      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set("ETag", object.httpEtag);
      headers.set("Access-Control-Allow-Origin", "*");
      headers.set("X-Content-Type-Options", "nosniff");
      headers.set("Cache-Control", /^(index|health)\.json$/.test(key) || key === "rejected.jsonl"
        ? "public, max-age=0, s-maxage=10, must-revalidate"
        : "public, max-age=31536000, immutable");
      response = new Response(object.body, { headers });
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }
    if (request.headers.get("If-None-Match") === response.headers.get("ETag")) {
      return new Response(null, { status: 304, headers: response.headers });
    }
    return request.method === "HEAD" ? new Response(null, response) : response;
  },
} satisfies ExportedHandler<SiteEnv>;
