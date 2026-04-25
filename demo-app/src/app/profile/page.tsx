import { auth } from "~/server/auth";
import { Sidebar } from "~/components/Sidebar";

export const metadata = { title: "Profile" };

export default async function ProfilePage() {
  const session = await auth();
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p>{session?.user?.email ?? "no email"}</p>
      </main>
    </div>
  );
}
