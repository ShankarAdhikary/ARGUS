import { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Called when the user presses Retry, before the children are re-rendered. */
  onRetry?: () => void;
  label?: string;
}

/** Catches render errors in a section so one broken card never blanks the page; offers a retry. */
export default class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  retry = () => {
    this.props.onRetry?.();
    this.setState({ failed: false });
  };

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="panel-error" role="alert">
        <span>Couldn't show {this.props.label ?? "this section"}.</span>
        <button type="button" className="link-btn" onClick={this.retry}>Retry</button>
      </div>
    );
  }
}
