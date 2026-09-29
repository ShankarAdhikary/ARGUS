import { ReactNode } from "react";
import { usePageTitle } from "../lib/usePageTitle";

interface Props {
  eyebrow: string;
  title: string;
  actions?: ReactNode;
  children?: ReactNode;
}

export default function PageHeader({ eyebrow, title, actions, children }: Props) {
  usePageTitle(title);
  return (
    <header className="page-header">
      <div className="page-header-left">
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {children && <p className="page-subtitle">{children}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
