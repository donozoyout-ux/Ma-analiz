export type ShareFeedback = "idle" | "copied" | "shared" | "error";

type ShareBrowser = {
  clipboard?: { writeText: (text: string) => Promise<void> };
  share?: (data: ShareData) => Promise<void>;
  canShare?: (data: ShareData) => boolean;
};

export async function copyMatchLink(browser: ShareBrowser, url: string): Promise<ShareFeedback> {
  try {
    if (!browser.clipboard?.writeText) return "error";
    await browser.clipboard.writeText(url);
    return "copied";
  } catch {
    return "error";
  }
}

export async function shareMatchLink(browser: ShareBrowser, data: ShareData & { url: string }): Promise<ShareFeedback> {
  try {
    if (!browser.share || (browser.canShare && !browser.canShare(data))) {
      return await copyMatchLink(browser, data.url);
    }
    await browser.share(data);
    return "shared";
  } catch (error) {
    // Cancellation is neutral and must never silently copy a declined share.
    return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError"
      ? "idle" : "error";
  }
}
