import { db } from "~/server/db";
import { Sidebar } from "~/components/Sidebar";

export const metadata = { title: "Projects" };

export default async function ProjectsPage() {
  void db;
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <p>Project list goes here.</p>
      </main>
    </div>
  );
}
