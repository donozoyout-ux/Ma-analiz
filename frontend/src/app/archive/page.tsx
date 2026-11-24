import { selectedTab } from "@/lib/navigation";
import { SectionTabs } from "@/components/section-tabs";
import View0 from "@/components/section-views/archive";
import View1 from "@/components/section-views/storage";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
 const active = selectedTab("archive", (await searchParams).tab);
 return <><SectionTabs section="archive" active={active} />{active === "storage" ? <View1 /> : <View0 />}</>;
}
