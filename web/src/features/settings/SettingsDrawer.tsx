import {
  CheckCircleIcon,
  DatabaseIcon,
  GaugeIcon,
  KeyIcon,
  MicrophoneIcon,
  SlidersHorizontalIcon,
  WarningCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "../../app/ThemeProvider";
import { ActivityIndicator } from "../../shared/components/ActivityIndicator";
import { getSettings, updateSettings, type SettingsPatch, type SettingsView } from "./api";
import "./settings-drawer.css";

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
}

const ROLE_LABELS = {
  basic: "基础推理",
  enrich: "出题富化",
  speech: "语音识别",
} as const;

const DATA_LOCATION_LABELS = {
  learning: "学习数据",
  trace: "运行轨迹",
  voice: "语音审计",
} as const;

export function SettingsDrawer({ open, onClose }: SettingsDrawerProps) {
  const { theme, toggleTheme } = useTheme();
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const updateQueue = useRef<Promise<void>>(Promise.resolve());
  const pendingUpdates = useRef(0);
  const desiredPreferences = useRef<SettingsPatch>({});

  useEffect(() => {
    if (!open) return;
    let active = true;
    void getSettings()
      .then((loaded) => {
        if (active) {
          setSettings(loaded);
          desiredPreferences.current = {};
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取设置");
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [open]);

  const patch = (command: SettingsPatch) => {
    desiredPreferences.current = { ...desiredPreferences.current, ...command };
    setSettings((current) =>
      current === null
        ? current
        : {
            ...current,
            preferences: { ...current.preferences, ...command },
          },
    );
    pendingUpdates.current += 1;
    setBusy(true);
    setError(null);
    const request = updateQueue.current.then(async () => {
      try {
        const saved = await updateSettings(command);
        setSettings({
          ...saved,
          preferences: {
            ...saved.preferences,
            ...desiredPreferences.current,
          },
        });
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "设置保存失败");
        desiredPreferences.current = {};
        try {
          setSettings(await getSettings());
        } catch {
          // Keep the original save error visible; a later reopen retries the read.
        }
      }
    });
    updateQueue.current = request.finally(() => {
      pendingUpdates.current -= 1;
      if (pendingUpdates.current === 0) {
        desiredPreferences.current = {};
        setBusy(false);
      }
    });
  };

  if (!open) return null;

  return (
    <div className="settings-layer" role="presentation">
      <button className="settings-layer__scrim" type="button" aria-label="关闭应用设置" onClick={onClose} />
      <section className="settings-drawer" role="dialog" aria-modal="true" aria-label="应用设置">
        <header className="settings-drawer__header">
          <div>
            <p>LOCAL CONTROL DECK</p>
            <h2>设置与运行偏好</h2>
          </div>
          <button type="button" aria-label="关闭应用设置" onClick={onClose}>
            <XIcon aria-hidden size={18} />
          </button>
        </header>

        <div className="settings-drawer__body">
          {settings !== null ? (
            busy ? (
              <ActivityIndicator
                className="settings-save-state"
                label="正在保存设置…"
                tone="brass"
              />
            ) : (
              <span className="settings-save-state" role="status">
                设置已保存
              </span>
            )
          ) : null}
          {error !== null ? (
            <p className="settings-error" role="alert">
              <WarningCircleIcon aria-hidden />
              {error}
            </p>
          ) : settings === null ? (
            <ActivityIndicator
              className="settings-empty"
              label="正在读取本地设置..."
              detail="密钥原文不会进入浏览器。"
              variant="block"
              tone="brass"
            />
          ) : (
            <>
              <section className="settings-section" aria-labelledby="settings-preferences">
                <div className="settings-section__heading">
                  <SlidersHorizontalIcon aria-hidden size={21} weight="duotone" />
                  <div>
                    <h3 id="settings-preferences">使用偏好</h3>
                    <p>即时生效，并保存在本机 learning.db。</p>
                  </div>
                </div>
                <div className="settings-row">
                  <div><strong>界面主题</strong><span>这是当前浏览器的显示偏好。</span></div>
                  <button type="button" className="settings-choice" onClick={toggleTheme}>
                    当前：{theme === "dark" ? "暗色" : "亮色"}
                  </button>
                </div>
                <div className="settings-row">
                  <div><strong>出题语言</strong><span>Web 与 CLI 共用同一 Preference Memory。</span></div>
                  <select
                    aria-label="出题语言"
                    value={settings.preferences.question_language}
                    onChange={(event) => patch({ question_language: event.target.value as "中文" | "英文" })}
                  >
                    <option value="中文">中文</option><option value="英文">英文</option>
                  </select>
                </div>
              </section>

              <section className="settings-section" aria-labelledby="settings-difficulty">
                <div className="settings-section__heading">
                  <GaugeIcon aria-hidden size={21} weight="duotone" />
                  <div><h3 id="settings-difficulty">考核难度</h3><p>{settings.difficulty.item_count} 个知识点 · 平均 {settings.difficulty.average_tier?.toFixed(2) ?? "—"} 档</p></div>
                </div>
                <div className="settings-radio-group" role="radiogroup" aria-label="难度倾向">
                  {([
                    ["foundation", "偏基础", "有效难度降低一档"],
                    ["adaptive", "自适应", "按学习表现原样出题"],
                    ["challenge", "偏挑战", "有效难度提高一档"],
                  ] as const).map(([value, label, help]) => (
                    <label key={value}>
                      <input
                        type="radio"
                        name="difficulty-mode"
                        value={value}
                        aria-label={label}
                        checked={settings.preferences.difficulty_mode === value}
                        onChange={() => patch({ difficulty_mode: value })}
                      />
                      <span><strong>{label}</strong><small>{help}</small></span>
                    </label>
                  ))}
                </div>
                <p className="settings-note">不会改写每个知识点的 1–5 档历史；只在下一次出题时做有界偏移。</p>
              </section>

              <section className="settings-section" aria-labelledby="settings-voice">
                <div className="settings-section__heading">
                  <MicrophoneIcon aria-hidden size={21} weight="duotone" />
                  <div><h3 id="settings-voice">语音与材料词表</h3><p>把当前题目对应材料的术语送给 ASR，改善专名识别。</p></div>
                </div>
                <label className="settings-switch">
                  <div><strong>启用材料词表</strong><span>就是 Prototype 里 hints on/off 的正式产品开关。</span></div>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="启用材料词表"
                    checked={settings.preferences.asr_material_hints_enabled}
                    onChange={(event) => patch({ asr_material_hints_enabled: event.target.checked })}
                  />
                </label>
                <p className="settings-note">每次 VoiceRun 冻结本题最多 50 个术语；已开始的录音不受随后切换影响。</p>
              </section>

              <section className="settings-section" aria-labelledby="settings-providers">
                <div className="settings-section__heading">
                  <KeyIcon aria-hidden size={21} weight="duotone" />
                  <div><h3 id="settings-providers">Provider 与密钥</h3><p>这里只展示安全状态，密钥值永远不进入浏览器。</p></div>
                </div>
                <div className="settings-providers">
                  {settings.providers.map((provider) => (
                    <article key={provider.role}>
                      <div><strong>{ROLE_LABELS[provider.role]}</strong><span>{provider.model ?? "未识别模型"}</span></div>
                      <div className="settings-provider__status">
                        {provider.configured ? <CheckCircleIcon aria-hidden size={16} /> : <WarningCircleIcon aria-hidden size={16} />}
                        <span>{provider.configured ? "已配置" : "未配置"}</span>
                        <small>由 .env 管理</small>
                      </div>
                      <code>{provider.endpoint_host ?? "—"}</code>
                    </article>
                  ))}
                </div>
                <p className="settings-note">需要更换 Key 或模型时编辑项目根目录 `.env` 后重启服务；页面不会读取或保存密钥原文。</p>
              </section>

              {settings.data_locations === null || settings.data_locations === undefined ? null : (
                <section className="settings-section" aria-labelledby="settings-data-locations">
                  <div className="settings-section__heading">
                    <DatabaseIcon aria-hidden size={21} weight="duotone" />
                    <div>
                      <h3 id="settings-data-locations">本机数据位置</h3>
                      <p>仅在 loopback 连接中显示；路径只读。</p>
                    </div>
                  </div>
                  <dl className="settings-data-locations">
                    {settings.data_locations.map((location) => (
                      <div key={location.kind}>
                        <dt>{DATA_LOCATION_LABELS[location.kind]}</dt>
                        <dd><code>{location.path}</code></dd>
                      </div>
                    ))}
                  </dl>
                  <p className="settings-note">要迁移数据，请停止服务后在操作系统中处理；页面不会改写这些位置。</p>
                </section>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
