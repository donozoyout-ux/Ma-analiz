import { selectedTab } from "@/lib/navigation";
import { SectionTabs } from "@/components/section-tabs";
import View0 from "@/components/section-views/performance";
import View1 from "@/components/section-views/lessons";
import Discipline from "@/components/section-views/discipline";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
 const active = selectedTab("analysis", (await searchParams).tab);
 return <><SectionTabs section="analysis" active={active} />{active === "lessons" ? <View1 /> : active === "discipline" ? <Discipline /> : <View0 />}</>;
}
