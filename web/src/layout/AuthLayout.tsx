import { useEffect, type ReactNode } from "react";
import { Brand } from "./Shell";

export default function AuthLayout({ title, lede, children, footer }: { title: string; lede?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · bdpay cloud`;
  }, [title]);
  return (
    <div className="authbox">
      <div style={{ marginBottom: 20 }}><Brand /></div>
      <div className="card">
        <h1 style={{ fontSize: 26 }}>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
        {children}
      </div>
      {footer && <p className="small mute" style={{ marginTop: 14, textAlign: "center" }}>{footer}</p>}
    </div>
  );
}
