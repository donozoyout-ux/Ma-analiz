import { selectedTab } from "@/lib/navigation";
import { SectionTabs } from "@/components/section-tabs";
import View0 from "@/components/section-views/predictions";
import View1 from "@/components/section-views/coupons";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
 const active = selectedTab("predictions", (await searchParams).tab);
 return <><SectionTabs section="predictions" active={active} />{active === "coupons" ? <View1 /> : <View0 />}</>;
}
