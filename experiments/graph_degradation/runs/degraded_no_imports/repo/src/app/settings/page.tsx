import { Sidebar } from "~/components/Sidebar";

export const metadata = {
  title: "Settings",
};

export default function SettingsPage() {
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <section className="mt-4 space-y-4">
          <p>Profile, account, and notification preferences live here.</p>
        </section>
      </main>
    </div>
  );
}
