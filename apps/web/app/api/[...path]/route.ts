import { proxyToApi } from "@/lib/proxy";

type Context = { params: Promise<{ path: string[] }> };

const forward = async (request: Request, context: Context) =>
  proxyToApi(request, (await context.params).path);

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;

export const dynamic = "force-dynamic";
