import { create } from 'zustand';
export const useConsoleStore = create((set) => ({
    selectedTopologyId: '',
    selectedSwitchId: '',
    setSelectedTopologyId: (selectedTopologyId) => set({ selectedTopologyId }),
    setSelectedSwitchId: (selectedSwitchId) => set({ selectedSwitchId }),
}));
