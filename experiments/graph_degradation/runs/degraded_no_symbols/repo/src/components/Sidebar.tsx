import Link from "next/link";

export interface SidebarItem {
  href: string;
  label: string;
}

export const NAV_ITEMS: SidebarItem[] = [
  { href: "/", label: "Home" },
  { href: "/settings", label: "Settings" },
];

export function Sidebar({ items = NAV_ITEMS }: { items?: SidebarItem[] }) {
  return (
    <nav className="flex flex-col gap-2 p-4">
      {items.map((item) => (
        <Link key={item.href} href={item.href} className="text-sm">
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
