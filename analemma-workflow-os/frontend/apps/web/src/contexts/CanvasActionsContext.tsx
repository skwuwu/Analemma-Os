import { createContext, useContext } from 'react';

interface CanvasActions {
  deleteNode: (id: string) => void;
}

export const CanvasActionsContext = createContext<CanvasActions>({
  deleteNode: () => {},
});

export const useCanvasActions = () => useContext(CanvasActionsContext);
