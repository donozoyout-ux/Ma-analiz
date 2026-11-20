import {ConnectionChecks} from "@/components/connection-checks";
import {HealthPanel} from "@/components/health";
export default function System(){return <><header className="topbar"><div><div className="eyebrow">Operations</div><h1 className="title">System health</h1></div></header><ConnectionChecks/><div className="card"><HealthPanel/></div></>}
