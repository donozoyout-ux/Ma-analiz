"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Bell,
  BellOff,
  CheckCircle2,
  AlertTriangle,
  ShieldCheck,
  Send,
  Plus,
  X,
  Volume2,
  Activity,
  Trophy,
  BrainCircuit,
  HeartHandshake,
} from "lucide-react";

interface NotificationTypes {
  match_start_reminder: boolean;
  verified_goal: boolean;
  verified_match_result: boolean;
  favorite_team_updates: boolean;
  ai_analysis_complete: boolean;
}

const DEFAULT_TYPES: NotificationTypes = {
  match_start_reminder: true,
  verified_goal: true,
  verified_match_result: true,
  favorite_team_updates: true,
  ai_analysis_complete: true,
};

const POPULAR_TEAMS = [
  "Galatasaray",
  "Fenerbahçe",
  "Beşiktaş",
  "Trabzonspor",
  "Real Madrid",
  "Manchester City",
  "Bayern Münih",
  "Barcelona",
];

export function NotificationSettings() {
  const [permission, setPermission] = useState<NotificationPermission>("default");
  const [isSupported, setIsSupported] = useState<boolean>(true);
  const [isSubscribed, setIsSubscribed] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<{ type: "success" | "error" | "info"; text: string } | null>(null);

  const [notificationTypes, setNotificationTypes] = useState<NotificationTypes>(DEFAULT_TYPES);
  const [favoriteTeams, setFavoriteTeams] = useState<string[]>([]);
  const [teamInput, setTeamInput] = useState<string>("");

  // Check client support and current permission on mount (NEVER prompts permission)
  useEffect(() => {
    if (typeof window !== "undefined") {
      if (!("Notification" in window) || !("serviceWorker" in navigator)) {
        setIsSupported(false);
        return;
      }
      setPermission(Notification.permission);

      // Check existing subscription
      navigator.serviceWorker.ready.then((reg) => {
        reg.pushManager.getSubscription().then((sub) => {
          if (sub) {
            setIsSubscribed(true);
            // Load saved preferences from local storage or server
            const savedPrefs = localStorage.getItem("mac_analiz_push_prefs");
            if (savedPrefs) {
              try {
                const parsed = JSON.parse(savedPrefs);
                if (parsed.types) setNotificationTypes(parsed.types);
                if (parsed.teams) setFavoriteTeams(parsed.teams);
              } catch (_) {}
            }
          }
        });
      });
    }
  }, []);

  const savePreferencesLocally = (types: NotificationTypes, teams: string[]) => {
    localStorage.setItem("mac_analiz_push_prefs", JSON.stringify({ types, teams }));
  };

  // User-explicit permission request and subscription
  const handleEnableNotifications = async () => {
    if (!isSupported) {
      setStatusMessage({ type: "error", text: "Bu tarayıcı veya cihaz Web Push bildirimlerini desteklemiyor." });
      return;
    }

    setIsLoading(true);
    setStatusMessage(null);

    try {
      // 1. User explicitly requested permission
      const perm = await Notification.requestPermission();
      setPermission(perm);

      if (perm !== "granted") {
        setStatusMessage({
          type: "error",
          text: "Bildirim izni verilmedi. Tarayıcı veya Android uygulama ayarlarından izinleri açabilirsiniz.",
        });
        setIsLoading(false);
        return;
      }

      // 2. Fetch VAPID public key from backend
      let vapidPublicKey = "";
      try {
        const keyRes = await fetch("/api/v1/push/public-key");
        if (keyRes.ok) {
          const keyData = await keyRes.json();
          vapidPublicKey = keyData.public_key || "";
        }
      } catch (err) {
        console.warn("VAPID public key fetch fallback:", err);
      }

      // 3. Register push manager
      const reg = await navigator.serviceWorker.ready;
      let subscription = await reg.pushManager.getSubscription();

      if (!subscription && vapidPublicKey) {
        // Convert base64 url safe VAPID key to Uint8Array
        const convertedKey = urlBase64ToUint8Array(vapidPublicKey);
        subscription = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: convertedKey,
        });
      }

      // 4. Send subscription to backend if available
      if (subscription) {
        const subData = subscription.toJSON();
        const clientToken = localStorage.getItem("mac_analiz_push_token") || generateClientToken();
        localStorage.setItem("mac_analiz_push_token", clientToken);

        await fetch("/api/v1/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            endpoint: subData.endpoint,
            keys: subData.keys,
            client_token: clientToken,
            preferences: {
              enabled: true,
              favorite_teams: favoriteTeams,
              notification_types: notificationTypes,
            },
          }),
        }).catch((e) => console.warn("Backend subscribe sync:", e));
      }

      setIsSubscribed(true);
      savePreferencesLocally(notificationTypes, favoriteTeams);
      setStatusMessage({
        type: "success",
        text: "Bildirimler başarıyla etkinleştirildi! Maç başlangıçları, goller ve analizler anında iletilecektir.",
      });
    } catch (err: any) {
      console.error("Bildirim etkinleştirme hatası:", err);
      setStatusMessage({
        type: "error",
        text: "Bildirim servisi bağlanırken bir hata oluştu: " + (err?.message || "Bilinmeyen hata"),
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Revoke / Unsubscribe
  const handleDisableNotifications = async () => {
    setIsLoading(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const endpoint = sub.endpoint;
        const clientToken = localStorage.getItem("mac_analiz_push_token");
        await sub.unsubscribe();
        if (clientToken) {
          await fetch("/api/v1/push/unsubscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint, client_token: clientToken }),
          }).catch(() => {});
        }
      }
      setIsSubscribed(false);
      localStorage.removeItem("mac_analiz_push_prefs");
      setStatusMessage({ type: "info", text: "Bildirim aboneliği başarıyla iptal edildi." });
    } catch (err: any) {
      setStatusMessage({ type: "error", text: "Abonelik iptal edilirken hata oluştu: " + err.message });
    } finally {
      setIsLoading(false);
    }
  };

  // Toggle specific notification type
  const toggleType = (key: keyof NotificationTypes) => {
    const updated = { ...notificationTypes, [key]: !notificationTypes[key] };
    setNotificationTypes(updated);
    savePreferencesLocally(updated, favoriteTeams);
    syncPreferencesToServer(updated, favoriteTeams);
  };

  // Team management
  const addTeam = (teamName: string) => {
    const trimmed = teamName.trim();
    if (!trimmed || favoriteTeams.includes(trimmed)) return;
    const updated = [...favoriteTeams, trimmed];
    setFavoriteTeams(updated);
    setTeamInput("");
    savePreferencesLocally(notificationTypes, updated);
    syncPreferencesToServer(notificationTypes, updated);
  };

  const removeTeam = (teamName: string) => {
    const updated = favoriteTeams.filter((t) => t !== teamName);
    setFavoriteTeams(updated);
    savePreferencesLocally(notificationTypes, updated);
    syncPreferencesToServer(notificationTypes, updated);
  };

  const syncPreferencesToServer = async (types: NotificationTypes, teams: string[]) => {
    const clientToken = localStorage.getItem("mac_analiz_push_token");
    if (!clientToken) return;
    try {
      await fetch("/api/v1/push/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_token: clientToken,
          enabled: isSubscribed,
          favorite_teams: teams,
          notification_types: types,
        }),
      });
    } catch (_) {}
  };

  // Send a test notification (strictly client-side mock to preview appearance)
  const sendTestNotification = async () => {
    if (!("Notification" in window) || Notification.permission !== "granted") {
      setStatusMessage({
        type: "error",
        text: "Önce bildirim izni vermeniz gerekmektedir.",
      });
      return;
    }

    try {
      const reg = await navigator.serviceWorker.ready;
      reg.showNotification("⚽ Doğrulanmış Gol! (Test)", {
        body: "Galatasaray 1 - 0 Fenerbahçe (28' - Mauro Icardi). Bildirim sisteminiz kusursuz çalışıyor!",
        icon: "/icon-192.png",
        badge: "/icon-192.png",
        tag: "test-notification-" + Date.now(),
        data: { url: "/matches" },
      });
      setStatusMessage({
        type: "success",
        text: "Test bildirimi cihazınıza gönderildi! Bildirim panelinizi kontrol edin.",
      });
    } catch (err: any) {
      setStatusMessage({
        type: "error",
        text: "Test bildirimi gösterilemedi: " + err.message,
      });
    }
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto py-2">
      {/* Top Header */}
      <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-xl bg-red-50 border border-red-100 flex items-center justify-center text-red-600 shrink-0">
              <Bell size={24} />
            </div>
            <div>
              <span className="text-xs font-bold tracking-wider text-red-600 uppercase">
                BİLDİRİM VE UYARI SİSTEMİ
              </span>
              <h2 className="text-xl font-extrabold text-slate-900 tracking-tight">
                Anlık Bildirim Ayarları
              </h2>
              <p className="text-xs sm:text-sm text-slate-500 mt-0.5">
                Doğrulanmış goller, maç başlangıçları ve AI tahminleri anında cebinizde.
              </p>
            </div>
          </div>

          {/* Master Switch Button */}
          <div className="flex items-center gap-3">
            {isSubscribed ? (
              <button
                type="button"
                onClick={handleDisableNotifications}
                disabled={isLoading}
                className="inline-flex items-center gap-2 px-4 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-semibold rounded-xl transition"
              >
                <BellOff size={16} />
                Bildirimleri Kapat
              </button>
            ) : (
              <button
                type="button"
                onClick={handleEnableNotifications}
                disabled={isLoading}
                className="inline-flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 text-white text-sm font-bold rounded-xl transition shadow-sm active:scale-95"
              >
                <Bell size={16} />
                {isLoading ? "İzin İsteniyor..." : "Bildirimleri Etkinleştir"}
              </button>
            )}
          </div>
        </div>

        {/* Status Message Alert */}
        {statusMessage && (
          <div
            className={`mt-4 p-3.5 rounded-xl text-xs sm:text-sm flex items-start gap-3 ${
              statusMessage.type === "success"
                ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
                : statusMessage.type === "error"
                ? "bg-rose-50 border border-rose-200 text-rose-800"
                : "bg-blue-50 border border-blue-200 text-blue-800"
            }`}
          >
            {statusMessage.type === "success" ? (
              <CheckCircle2 size={18} className="shrink-0 text-emerald-600 mt-0.5" />
            ) : statusMessage.type === "error" ? (
              <AlertTriangle size={18} className="shrink-0 text-rose-600 mt-0.5" />
            ) : (
              <ShieldCheck size={18} className="shrink-0 text-blue-600 mt-0.5" />
            )}
            <div className="flex-1 font-medium">{statusMessage.text}</div>
            <button
              onClick={() => setStatusMessage(null)}
              className="text-slate-400 hover:text-slate-600"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {/* System Permission Badge */}
        <div className="mt-5 pt-4 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500">
          <div className="flex items-center gap-2">
            <span>Cihaz İzin Durumu:</span>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full font-bold ${
                permission === "granted"
                  ? "bg-emerald-100 text-emerald-700"
                  : permission === "denied"
                  ? "bg-rose-100 text-rose-700"
                  : "bg-slate-100 text-slate-700"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  permission === "granted"
                    ? "bg-emerald-500"
                    : permission === "denied"
                    ? "bg-rose-500"
                    : "bg-slate-400"
                }`}
              />
              {permission === "granted"
                ? "İzin Verildi"
                : permission === "denied"
                ? "Engellendi (Ayarlardan açın)"
                : "Henüz İstenmedi"}
            </span>
          </div>

          {permission === "granted" && (
            <button
              type="button"
              onClick={sendTestNotification}
              className="inline-flex items-center gap-1.5 text-red-600 hover:text-red-700 font-semibold"
            >
              <Send size={14} />
              Test Bildirimi Gönder
            </button>
          )}
        </div>
      </div>

      {/* Notification Categories Grid */}
      <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
        <h3 className="text-base font-bold text-slate-900 mb-4 flex items-center gap-2">
          <Volume2 size={18} className="text-red-600" />
          Bildirim Türleri
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Match Start Reminder */}
          <div
            onClick={() => toggleType("match_start_reminder")}
            className="flex items-start justify-between p-4 rounded-xl border border-slate-200 hover:border-slate-300 bg-slate-50/50 cursor-pointer transition select-none"
          >
            <div className="flex items-start gap-3">
              <div className="p-2 bg-red-100 text-red-700 rounded-lg shrink-0 mt-0.5">
                <Activity size={18} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-800">Maç Başlama Hatırlatması</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  Takip ettiğiniz maçların başlamasına 15 dakika kala bildirim alın.
                </div>
              </div>
            </div>
            <input
              type="checkbox"
              readOnly
              checked={notificationTypes.match_start_reminder}
              className="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500 pointer-events-none accent-red-600"
            />
          </div>

          {/* Verified Goal Alert */}
          <div
            onClick={() => toggleType("verified_goal")}
            className="flex items-start justify-between p-4 rounded-xl border border-slate-200 hover:border-slate-300 bg-slate-50/50 cursor-pointer transition select-none"
          >
            <div className="flex items-start gap-3">
              <div className="p-2 bg-emerald-100 text-emerald-700 rounded-lg shrink-0 mt-0.5">
                <Trophy size={18} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-800">Doğrulanmış Gol Bildirimi</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  Yalnızca doğrulanmış skor ve golcü bilgisi içeren anlık gol uyarıları.
                </div>
              </div>
            </div>
            <input
              type="checkbox"
              readOnly
              checked={notificationTypes.verified_goal}
              className="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500 pointer-events-none accent-red-600"
            />
          </div>

          {/* Verified Match Result */}
          <div
            onClick={() => toggleType("verified_match_result")}
            className="flex items-start justify-between p-4 rounded-xl border border-slate-200 hover:border-slate-300 bg-slate-50/50 cursor-pointer transition select-none"
          >
            <div className="flex items-start gap-3">
              <div className="p-2 bg-blue-100 text-blue-700 rounded-lg shrink-0 mt-0.5">
                <CheckCircle2 size={18} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-800">Doğrulanmış Maç Sonucu</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  Hakem düdüğü çaldıktan ve skor doğrulandıktan sonra kesin sonuç özeti.
                </div>
              </div>
            </div>
            <input
              type="checkbox"
              readOnly
              checked={notificationTypes.verified_match_result}
              className="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500 pointer-events-none accent-red-600"
            />
          </div>

          {/* AI Analysis Completed */}
          <div
            onClick={() => toggleType("ai_analysis_complete")}
            className="flex items-start justify-between p-4 rounded-xl border border-slate-200 hover:border-slate-300 bg-slate-50/50 cursor-pointer transition select-none"
          >
            <div className="flex items-start gap-3">
              <div className="p-2 bg-purple-100 text-purple-700 rounded-lg shrink-0 mt-0.5">
                <BrainCircuit size={18} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-800">AI Analiz Tamamlandı</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  Algoritma ve yapay zekâ maç analizini tamamlayıp kanıt ürettiğinde bildirim.
                </div>
              </div>
            </div>
            <input
              type="checkbox"
              readOnly
              checked={notificationTypes.ai_analysis_complete}
              className="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500 pointer-events-none accent-red-600"
            />
          </div>

          {/* Favorite Team Updates */}
          <div
            onClick={() => toggleType("favorite_team_updates")}
            className="md:col-span-2 flex items-start justify-between p-4 rounded-xl border border-slate-200 hover:border-slate-300 bg-slate-50/50 cursor-pointer transition select-none"
          >
            <div className="flex items-start gap-3">
              <div className="p-2 bg-amber-100 text-amber-700 rounded-lg shrink-0 mt-0.5">
                <HeartHandshake size={18} />
              </div>
              <div>
                <div className="text-sm font-bold text-slate-800">Favori Takım Özel Bildirimleri</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  Aşağıda belirlediğiniz favori takımlara ait tüm kritik gelişmeler öncelikli bildirilsin.
                </div>
              </div>
            </div>
            <input
              type="checkbox"
              readOnly
              checked={notificationTypes.favorite_team_updates}
              className="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500 pointer-events-none accent-red-600"
            />
          </div>
        </div>
      </div>

      {/* Favorite Teams Selector */}
      <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
        <h3 className="text-base font-bold text-slate-900 mb-2 flex items-center gap-2">
          <HeartHandshake size={18} className="text-red-600" />
          Takip Edilen Takımlar
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          Yalnızca seçtiğiniz takımların maçları için bildirim üretilmesini sağlayabilirsiniz. Takım seçmezseniz tüm önemli maçlar bildirilir.
        </p>

        {/* Selected Teams Tags */}
        <div className="flex flex-wrap gap-2 mb-4 min-h-[38px] p-2 bg-slate-50 border border-slate-200 rounded-xl">
          {favoriteTeams.length === 0 ? (
            <span className="text-xs text-slate-400 py-1 px-2 italic">
              Henüz takip edilen takım seçilmedi (tüm ligler genel bildirilir)
            </span>
          ) : (
            favoriteTeams.map((team) => (
              <span
                key={team}
                className="inline-flex items-center gap-1.5 px-3 py-1 bg-white border border-red-200 text-red-700 text-xs font-bold rounded-lg shadow-xs"
              >
                {team}
                <button
                  type="button"
                  onClick={() => removeTeam(team)}
                  className="hover:text-red-900 p-0.5 rounded-full hover:bg-red-50"
                  aria-label={`${team} takımını kaldır`}
                >
                  <X size={12} />
                </button>
              </span>
            ))
          )}
        </div>

        {/* Add Team Input */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addTeam(teamInput);
          }}
          className="flex gap-2 mb-4"
        >
          <input
            type="text"
            value={teamInput}
            onChange={(e) => setTeamInput(e.target.value)}
            placeholder="Takım adı girin (örn. Galatasaray, Arsenal...)"
            className="flex-1 px-3.5 py-2 text-xs sm:text-sm border border-slate-200 rounded-xl focus:outline-hidden focus:border-red-500 focus:ring-1 focus:ring-red-500"
          />
          <button
            type="submit"
            className="inline-flex items-center gap-1 px-4 py-2 bg-slate-900 hover:bg-black text-white text-xs sm:text-sm font-semibold rounded-xl transition"
          >
            <Plus size={16} />
            Ekle
          </button>
        </form>

        {/* Quick Suggestions */}
        <div>
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider block mb-2">
            Hızlı Ekle:
          </span>
          <div className="flex flex-wrap gap-1.5">
            {POPULAR_TEAMS.map((team) => {
              const isSelected = favoriteTeams.includes(team);
              return (
                <button
                  key={team}
                  type="button"
                  onClick={() => (isSelected ? removeTeam(team) : addTeam(team))}
                  className={`text-xs px-2.5 py-1 rounded-lg border font-medium transition ${
                    isSelected
                      ? "bg-red-50 border-red-300 text-red-700"
                      : "bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  {isSelected ? "✓ " : "+ "}
                  {team}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Security & Verification Guarantee */}
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-5 text-xs text-slate-600 space-y-2">
        <div className="font-bold text-slate-900 flex items-center gap-1.5">
          <ShieldCheck size={16} className="text-red-600" />
          Maç Analiz Doğruluk ve Güvenlik Güvencesi
        </div>
        <p>
          • <strong>Sahte Bildirim Engeli:</strong> Eksik, spekülatif veya doğrulanmamış gol, skor veya oran verileri için hiçbir koşulda bildirim üretilmez.
        </p>
        <p>
          • <strong>Mükerrer Bildirim Koruması:</strong> Aynı gol veya maç sonucu için kullanıcılara asla çift bildirim iletilmez.
        </p>
        <p>
          • <strong>Kullanıcı İzni:</strong> Bildirim izni yalnızca sizin açık onayınızla talep edilir ve dilediğiniz an bu ekrandan veya cihaz ayarlarından iptal edilebilir.
        </p>
      </div>
    </div>
  );
}

// Utility: convert VAPID base64 string to Uint8Array
function urlBase64ToUint8Array(base64String: string) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// Utility: generate local client token for auth
function generateClientToken() {
  const array = new Uint8Array(24);
  window.crypto.getRandomValues(array);
  return Array.from(array, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
