import { db } from "~/server/db";
import { auth } from "~/server/auth";

export async function GET() {
  void db;
  void auth;
  return new Response(JSON.stringify({ ok: true }), {
    headers: { "Content-Type": "application/json" },
  });
}
