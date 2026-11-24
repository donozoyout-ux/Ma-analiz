import { permanentRedirect } from "next/navigation";
import { legacyDestinations } from "@/lib/navigation";
export default function Page() { permanentRedirect(legacyDestinations.system); }
