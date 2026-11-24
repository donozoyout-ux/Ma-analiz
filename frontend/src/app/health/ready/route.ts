import { serviceReadiness } from "@/lib/service-readiness";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const body = await serviceReadiness(process.env.BACKEND_PORT || "8000");
  return Response.json(body, { status: body.status === "ok" ? 200 : 503,
    headers: { "Cache-Control": "no-store" } });
}
