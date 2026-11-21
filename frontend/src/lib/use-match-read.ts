"use client";
import { useCallback, useEffect, useState } from "react";
import { isMatchReadPayload } from "@/lib/matches";
export function useMatchRead<T>(endpoint: string) {
 const [data,setData] = useState<T | null>(null), [error,setError] = useState(false), [version,setVersion] = useState(0);
 const [loadedEndpoint,setLoadedEndpoint] = useState("");
 const refresh = useCallback(() => setVersion(value=>value+1),[]);
 useEffect(()=>{
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),12000);
  let active = true;
  setData(null); setError(false);
  fetch(`/api/v1/${endpoint}`,{signal:controller.signal,cache:"no-store"})
   .then(async response=>{if(!response.ok)throw new Error("MATCH_READ_FAILED");return response.json();})
   .then(value=>{if(!isMatchReadPayload(value,endpoint.startsWith("match-center?")))throw new Error("INVALID_MATCH_RESPONSE");if(active){setLoadedEndpoint(endpoint);setData(value as T);}})
   .catch(()=>{if(active)setError(true);}).finally(()=>clearTimeout(timer));
  return ()=>{active=false;clearTimeout(timer);controller.abort();};
 },[endpoint,version]);
 return {data:loadedEndpoint===endpoint?data:null,error,refresh};
}
