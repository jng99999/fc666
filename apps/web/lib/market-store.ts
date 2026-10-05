import {create} from "zustand";
import {keyFor,type Snapshot} from "./market";
type State={snapshots:Record<string,Snapshot>; connection:"connecting"|"connected"|"retrying"|"offline"; apply:(value:Snapshot)=>void; clear:(symbol:string,channel:string)=>void; setConnection:(value:State["connection"])=>void};
export const useMarketStore=create<State>((set) => ({
  snapshots:{},connection:"connecting",
  setConnection:connection => set({connection}),
  clear:(symbol,channel) => set(state => {const snapshots={...state.snapshots};delete snapshots[`${symbol}:${channel}`];return {snapshots};}),
  apply:value => set(state => {
    const key=keyFor(value.symbol,value.channel),previous=state.snapshots[key];
    if (previous?.generation===value.generation && previous.sequence>=value.sequence) return state;
    return {snapshots:{...state.snapshots,[key]:value}};
  }),
}));
