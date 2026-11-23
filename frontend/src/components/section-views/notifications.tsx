import { NotificationSettings } from "@/components/notification-settings";

export default function NotificationsView() {
  return (
    <>
      <header className="topbar">
        <div>
          <div className="eyebrow">MAÇANALİZ / İLETİŞİM</div>
          <h1 className="title">Push Bildirimleri</h1>
          <p className="page-intro">
            Gol, maç sonucu, maç başlama ve AI analiz bildirim tercihlerinizi yönetin.
          </p>
        </div>
      </header>
      <NotificationSettings />
    </>
  );
}
