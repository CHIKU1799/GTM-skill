// Vercel serverless proxy for Apollo.io API (bypasses CORS)
// POST /api/apollo  →  POST https://api.apollo.io/v1/mixed_people/search
// The API key is passed from the browser in the X-Api-Key header.

export default async function handler(req, res) {
  // CORS headers for browser access
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-Api-Key");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const apiKey = req.headers["x-api-key"];
  if (!apiKey) {
    return res.status(400).json({ error: "Missing X-Api-Key header" });
  }

  try {
    const upstream = await fetch("https://api.apollo.io/v1/mixed_people/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": apiKey,
      },
      body: JSON.stringify(req.body),
    });

    const data = await upstream.json();
    return res.status(upstream.status).json(data);
  } catch (e) {
    return res.status(502).json({ error: "Apollo proxy error: " + e.message });
  }
}
