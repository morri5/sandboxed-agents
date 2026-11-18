import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "sandboxed-agents-lab",
  description: "Chat with a repo; the agent works in a microVM.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
