import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";
import AppShell from "@/components/layout/AppShell";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { ChatRuntimeProvider } from "@/features/chat";

export default function WorkspaceLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CapabilityAccessProvider>
      <ChatRuntimeProvider>
        <AppShell sidebar={<WorkspaceSidebar />}>
          <CapabilityGate>{children}</CapabilityGate>
        </AppShell>
      </ChatRuntimeProvider>
    </CapabilityAccessProvider>
  );
}
