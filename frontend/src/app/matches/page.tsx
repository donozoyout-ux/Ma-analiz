import { selectedTab } from "@/lib/navigation";
import { SectionTabs } from "@/components/section-tabs";
import View0 from "@/components/section-views/matches";
import View1 from "@/components/section-views/odds";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
 const active = selectedTab("matches", (await searchParams).tab);
 return <><SectionTabs section="matches" active={active} />{active === "odds" ? <View1 /> : <View0 />}</>;
}
