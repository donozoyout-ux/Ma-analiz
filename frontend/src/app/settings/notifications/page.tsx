import { permanentRedirect } from "next/navigation";

export default function Page() {
  permanentRedirect("/management?tab=notifications");
}
