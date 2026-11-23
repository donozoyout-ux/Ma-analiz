import { selectedTab } from "@/lib/navigation";
import { SectionTabs } from "@/components/section-tabs";
import View0 from "@/components/section-views/system";
import View1 from "@/components/section-views/settings";

export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
  const active = selectedTab("management", (await searchParams).tab);
  return (
    <>
      <SectionTabs section="management" active={active} />
      {active === "settings" ? <View1 /> : <View0 />}
    </>
  );
}
