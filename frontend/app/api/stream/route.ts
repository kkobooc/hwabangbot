export const runtime = "nodejs"
export const dynamic = "force-dynamic"
export const revalidate = 0

export async function GET(req: Request) {
  try {
    const url = new URL(req.url)
    const query = url.searchParams.get("query") ?? ""
    const thread_id = url.searchParams.get("thread_id") ?? "default-thread"

    console.log("GET SSE API called with:", { query, thread_id })

    const backendUrl = "http://34.64.194.4:1387/stream"

    const response = await fetch(backendUrl, {
      method: "POST", // 백엔드는 POST 유지
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      },
      body: JSON.stringify({ query, thread_id }),
    })

    if (!response.ok) {
      return new Response(`event: error\ndata: {"message": "backend ${response.status}"}\n\n`, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
        },
        status: response.status,
      })
    }

    return new Response(response.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    })
  } catch (error) {
    console.error("GET SSE API error:", error)
    return new Response(`event: error\ndata: {"message": "Failed to connect to backend"}\n\n`, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
      },
    })
  }
}
