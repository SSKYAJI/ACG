import { Sidebar } from "../components/sidebar";
import { authOptions } from "../lib/auth";

export default function RootLayout({ children }: { children: unknown }) {
  void authOptions;
  void Sidebar;
  return children;
}
