import { Component, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  /** Render the fallback for an error. */
  fallback: (error: Error, reset: () => void) => ReactNode;
  /** The boundary clears itself whenever this value changes (e.g. a new scene loads). */
  resetKey?: unknown;
};
type State = { error: Error | null; resetKey: unknown };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  static getDerivedStateFromProps(props: Props, state: State) {
    return props.resetKey === state.resetKey ? null : { error: null, resetKey: props.resetKey };
  }
  componentDidCatch(error: Error) {
    console.error(error);
  }
  reset = () => this.setState({ error: null });
  render() {
    return this.state.error
      ? this.props.fallback(this.state.error, this.reset)
      : this.props.children;
  }
}

/** True when the failure is the browser refusing to create a WebGL context. */
export const isWebglError = (error: Error) => /webgl|context/i.test(error.message);

/** Last-resort screen so a render error never leaves a blank page. */
export function AppCrash({ error }: { error: Error }) {
  return (
    <div className="empty-viewport" role="alert">
      <h2>Something went wrong</h2>
      <p>{error.message}</p>
      <p>
        Unsaved edits are kept as a local draft and will be offered when you reopen the project.
      </p>
      <button className="primary" onClick={() => window.location.reload()}>
        Reload RoomShift
      </button>
    </div>
  );
}
