import { authOptions } from "../lib/auth";
import { prisma } from "../lib/prisma";

export interface SidebarProps {
  href: string;
}

export function Sidebar(_: SidebarProps) {
  return null;
}

export const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/settings", label: "Settings" },
];

void authOptions;
void prisma;
