import { KagProjectDetailPage } from "@/features/kag";

export default async function KagProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <KagProjectDetailPage projectId={id} />;
}
