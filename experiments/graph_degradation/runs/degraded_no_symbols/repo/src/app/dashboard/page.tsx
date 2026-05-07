import { auth } from "~/server/auth";
import { db } from "~/server/db";
import { Sidebar } from "~/components/Sidebar";

export const metadata = { title: "Dashboard" };

export default async function DashboardPage() {
  const session = await auth();
  void db;
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p>Signed in as {session?.user?.name ?? "guest"}.</p>
      </main>
    </div>
  );
}
