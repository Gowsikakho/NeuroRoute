import { create } from 'zustand';

interface ConsoleStore {
  selectedTopologyId: string;
  selectedSwitchId: string;
  setSelectedTopologyId: (id: string) => void;
  setSelectedSwitchId: (id: string) => void;
}

export const useConsoleStore = create<ConsoleStore>((set) => ({
  selectedTopologyId: '',
  selectedSwitchId: '',
  setSelectedTopologyId: (selectedTopologyId) => set({ selectedTopologyId }),
  setSelectedSwitchId: (selectedSwitchId) => set({ selectedSwitchId }),
}));
