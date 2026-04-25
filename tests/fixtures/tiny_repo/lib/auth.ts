import { PrismaAdapter } from "@auth/prisma-adapter";
import GoogleProvider from "next-auth/providers/google";
import { prisma } from "./prisma";

export const authOptions = {
  adapter: PrismaAdapter(prisma),
  providers: [GoogleProvider({ clientId: "", clientSecret: "" })],
};

export function getCurrentUser(): null {
  return null;
}
