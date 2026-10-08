import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import { useEditor } from './store';

// Browser end-to-end tests read editor state directly; never enabled in normal builds.
if (import.meta.env.VITE_E2E === 'true') (window as unknown as { __editor: typeof useEditor }).__editor = useEditor;

createRoot(document.getElementById('root')!).render(<App />);
