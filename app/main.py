import os, re, calendar
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, field_validator, ConfigDict
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}

@app.on_event("startup")
def indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="employee_code_unique")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="employee_department_code")
    db.employees.create_index([("joined_on", ASCENDING)], name="employee_joined_on")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="attendance_employee_date_unique")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_code")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", ASCENDING)], name="attendance_status_date")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="attendance_punch_lookup")

def clean(d):
    if not d: return d
    d = dict(d); d.pop("_id", None)
    for k in ("created_at","punch_in","punch_out"):
        if isinstance(d.get(k), datetime): d[k] = epoch(d[k])
    if "history" in d:
        h=[]
        for e in d.get("history") or []:
            e=dict(e)
            if isinstance(e.get("at"),datetime): e["at"]=epoch(e["at"])
            ch={}
            for k,v in (e.get("changes") or {}).items():
                ch[k]={x:(epoch(y) if isinstance(y,datetime) else y) for x,y in v.items()}
            e["changes"]=ch; h.append(e)
        d["history"]=h
    d.setdefault("history",[]); d.setdefault("half_day",False)
    return d

def epoch_ms_valid(v):
    if isinstance(v,bool) or not isinstance(v,int) or v < 0 or v < 100000000000 or v > 253402300799999:
        raise ValueError("must be epoch milliseconds")
    return v
def from_epoch(v):
    epoch_ms_valid(v)
    try: return datetime.fromtimestamp(v/1000, UTC).replace(microsecond=0)
    except (ValueError,OverflowError,OSError): raise HTTPException(422,"invalid epoch milliseconds")
def epoch(dt):
    if dt.tzinfo is None: dt=dt.replace(tzinfo=UTC)
    return int(dt.timestamp()*1000)
def now_utc(): return datetime.now(UTC).replace(microsecond=0)
def round2(x): return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
def round4(x): return float(Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
def parse_date(s):
    try:
        if not isinstance(s,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",s): raise ValueError()
        return date.fromisoformat(s)
    except Exception: raise HTTPException(422,"date must be YYYY-MM-DD")
def month_bounds(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",month): raise HTTPException(422,"month must be YYYY-MM")
    y,m=map(int,month.split("-")); start=date(y,m,1); end=date(y,m,calendar.monthrange(y,m)[1])
    return start,end
def workdays(start,end):
    if start>end:return 0
    return sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5)
def shift_datetime(day, hhmm):
    h,m=map(int,hhmm.split(":")); return datetime.combine(day,datetime.min.time(),IST).replace(hour=h,minute=m).astimezone(UTC)
def attendance_day(punch, emp):
    local=punch.astimezone(IST); d=local.date()
    sh, sm=map(int,emp["shift_start"].split(":")); eh, em=map(int,emp["shift_end"].split(":"))
    start=sh*60+sm; end=eh*60+em; t=local.hour*60+local.minute
    if end<=start and t<end: d-=timedelta(days=1)
    return d.isoformat()
def calc_late(pin, emp, dstr):
    start=shift_datetime(parse_date(dstr),emp["shift_start"])
    mins=int((pin-start).total_seconds()//60)
    return mins if (pin-start).total_seconds()>600 else 0
def calc_derived(pin,pout,emp,dstr):
    late=calc_late(pin,emp,dstr) if pin else 0
    if not pin or not pout:return {"work_hours":None,"late_minutes":late,"overtime_minutes":0,"half_day":False}
    hours=round2((pout-pin).total_seconds()/3600)
    endday=parse_date(dstr)
    if emp["shift_end"] <= emp["shift_start"]: endday+=timedelta(days=1)
    end=shift_datetime(endday,emp["shift_end"])
    overtime=int((pout-end).total_seconds()//60)
    return {"work_hours":hours,"late_minutes":late,"overtime_minutes":overtime if overtime>=30 else 0,"half_day":hours<4.5}

class EmployeeIn(BaseModel):
    emp_code:str; name:str; email:str; department:str
    shift_start:str="09:30"; shift_end:str="18:30"; joined_on:str
    @field_validator("joined_on")
    @classmethod
    def valid_join(cls,v): parse_date(v); return v
    @field_validator("shift_start","shift_end")
    @classmethod
    def valid_shift(cls,v):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",v): raise ValueError("must be HH:MM")
        return v
    @field_validator("email")
    @classmethod
    def valid_email(cls,v):
        if "@" not in v: raise ValueError("invalid email")
        return v
class PunchInIn(BaseModel):
    emp_code:str; punched_at:Optional[int]=None; status:Literal["PRESENT","WFH","ON_DUTY"]="PRESENT"
    @field_validator("punched_at")
    @classmethod
    def valid_ts(cls,v): return None if v is None else epoch_ms_valid(v)
class PunchOutIn(BaseModel):
    emp_code:str; punched_at:Optional[int]=None
    @field_validator("punched_at")
    @classmethod
    def valid_ts(cls,v): return None if v is None else epoch_ms_valid(v)
class RegularizeIn(BaseModel):
    status:Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]]=None
    punch_in:Optional[int]=None; punch_out:Optional[int]=None
    reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator("punch_in","punch_out")
    @classmethod
    def valid_ts(cls,v): return None if v is None else epoch_ms_valid(v)

@app.get("/health")
def health():
    try: db.command("ping"); return {"status":"ok"}
    except Exception: raise HTTPException(503,"MongoDB unavailable")

@app.post("/employees",status_code=201)
def create_employee(body:EmployeeIn):
    if body.shift_start==body.shift_end: raise HTTPException(422,"shift_start must differ from shift_end")
    doc=body.model_dump(); doc["created_at"]=now_utc()
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"emp_code already exists")
    return clean(doc)
@app.get("/employees")
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={}
    if department is not None:q["department"]=department
    total=db.employees.count_documents(q)
    items=[clean(x) for x in db.employees.find(q,{"_id":0}).sort("emp_code",1).skip((page-1)*page_size).limit(page_size)]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.post("/attendance/punch-in",status_code=201)
def punch_in(body:PunchInIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    ts=from_epoch(body.punched_at) if body.punched_at is not None else now_utc()
    d=attendance_day(ts,emp)
    derived=calc_derived(ts,None,emp,d)
    doc={"emp_code":body.emp_code,"date":d,"status":body.status,"punch_in":ts,"punch_out":None,**derived,"history":[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"already punched in for this date")
    return clean(doc)

@app.post("/attendance/punch-out")
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    ts=from_epoch(body.punched_at) if body.punched_at is not None else now_utc()
    rec=db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$lte":ts}},sort=[("punch_in",DESCENDING)])
    if not rec: raise HTTPException(404,"no punch-in found")
    if rec.get("punch_out") is not None: raise HTTPException(409,"already punched out")
    if ts<=rec["punch_in"] or ts-rec["punch_in"]>timedelta(hours=24): raise HTTPException(422,"punch-out must be after punch-in and within 24 hours")
    vals=calc_derived(rec["punch_in"],ts,emp,rec["date"])
    updated=db.attendance_logs.find_one_and_update({"_id":rec["_id"],"punch_out":None},{"$set":{"punch_out":ts,**vals}},return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409,"already punched out")
    return clean(updated)

@app.get("/attendance")
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={}
    if emp_code is not None:q["emp_code"]=emp_code
    if status is not None:
        if status not in STATUSES: raise HTTPException(422,"invalid status")
        q["status"]=status
    if date_from or date_to:
        q["date"]={}
        if date_from:q["date"]["$gte"]=parse_date(date_from).isoformat()
        if date_to:q["date"]["$lte"]=parse_date(date_to).isoformat()
        if date_from and date_to and date_from>date_to: raise HTTPException(422,"date_from must be <= date_to")
    total=db.attendance_logs.count_documents(q)
    items=[clean(x) for x in db.attendance_logs.find(q,{"_id":0}).sort([("date",-1),("emp_code",1)]).skip((page-1)*page_size).limit(page_size)]
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code:str,date:str,body:RegularizeIn):
    day=parse_date(date).isoformat()
    emp=db.employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    changes_req=body.model_dump(exclude_unset=True)
    changes_req.pop("reason",None); changes_req.pop("regularized_by",None)
    # The contract rejects punch fields supplied alongside ABSENT/LEAVE, even if null.
    requested_status = changes_req.get("status")
    if requested_status in ("ABSENT", "LEAVE") and ("punch_in" in changes_req or "punch_out" in changes_req):
        raise HTTPException(422, "ABSENT/LEAVE corrections must not supply punch fields")
    update={}
    for k,v in changes_req.items():
        update[k]=from_epoch(v) if k in ("punch_in","punch_out") and v is not None else v
    # Atomic optimistic loop prevents concurrent corrections overwriting audit entries.
    for _ in range(5):
        rec=db.attendance_logs.find_one({"emp_code":emp_code,"date":day})
        if not rec: raise HTTPException(404,"attendance record not found")
        new_status=update.get("status",rec["status"])
        pin=update.get("punch_in",rec.get("punch_in")); pout=update.get("punch_out",rec.get("punch_out"))
        if new_status in ("ABSENT","LEAVE"): pin=pout=None
        elif pin is None: raise HTTPException(422,"presence status requires punch_in")
        if pin and attendance_day(pin, emp) != day:
            raise HTTPException(422, "punch_in must belong to the attendance date")
        if pin and pout and (pout<=pin or pout-pin>timedelta(hours=24)): raise HTTPException(422,"invalid punch range")
        vals=calc_derived(pin,pout,emp,day)
        newvals={"status":new_status,"punch_in":pin,"punch_out":pout,**vals}
        changed={}
        for k,v in newvals.items():
            old=rec.get(k)
            if old!=v: changed[k]={"from":old,"to":v}
        if not changed: raise HTTPException(422, "regularization does not change any fields")
        hist={"at":now_utc(),"by":body.regularized_by,"reason":body.reason,"changes":changed}
        filt={"_id":rec["_id"],"history":rec.get("history",[])}
        res=db.attendance_logs.update_one(filt,{"$set":newvals,"$push":{"history":hist}})
        if res.modified_count: return clean(db.attendance_logs.find_one({"_id":rec["_id"]}))
    raise HTTPException(409,"record changed concurrently; retry")

def employee_monthly_pipeline(emp_code,month):
    start,end=month_bounds(month)
    return [{"$match":{"emp_code":emp_code,"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}},{"$group":{"_id":None,"present_days":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},[2,3,4,5,6]]}]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},"leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},"late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},"total_overtime_minutes":{"$sum":{"$ifNull":["$overtime_minutes",0]}}}}]
def monthly_data(emp_code,month):
    start,end=month_bounds(month); emp=db.employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    wd=workdays(max(start,parse_date(emp["joined_on"])),end)
    rows=list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code,month)))
    r=rows[0] if rows else {}
    present=round2(r.get("present_days",0))
    return {"emp_code":emp_code,"month":month,"working_days":wd,"present_days":present,"leave_days":r.get("leave_days",0),"late_count":r.get("late_count",0),"total_late_minutes":r.get("total_late_minutes",0),"total_overtime_minutes":r.get("total_overtime_minutes",0),"attendance_pct":round4(present/wd*100) if wd else None}
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code:str,month:str=Query(...,pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    return monthly_data(emp_code,month)

@app.get("/analytics/departments/summary")
def department_summary(month:str=Query(...,pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),department:Optional[str]=None):
    start,end=month_bounds(month)
    empmatch={"joined_on":{"$lte":end.isoformat()}}
    if department is not None:empmatch["department"]=department
    # Employee-led lookup preserves employees with no attendance records in headcount.
    pipeline=[{"$match":empmatch},{"$lookup":{"from":"attendance_logs","let":{"code":"$emp_code"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$code"]},{"$gte":["$date",start.isoformat()]},{"$lte":["$date",end.isoformat()]}]}}},{"$group":{"_id":None,"present_days":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},[2,3,4,5,6]]}]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},"work_sum":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$ne":["$work_hours",None]}]},"$work_hours",0]}},"work_count":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$ne":["$work_hours",None]}]},1,0]}},"late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}},"late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},"leave_count":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},"on_duty_count":{"$sum":{"$cond":[{"$eq":["$status","ON_DUTY"]},1,0]}}}}],"as":"stats"}},{"$unwind":{"path":"$stats","preserveNullAndEmptyArrays":True}},{"$group":{"_id":"$department","headcount":{"$sum":1},"present_days":{"$sum":{"$ifNull":["$stats.present_days",0]}},"work_sum":{"$sum":{"$ifNull":["$stats.work_sum",0]}},"work_count":{"$sum":{"$ifNull":["$stats.work_count",0]}},"late_count":{"$sum":{"$ifNull":["$stats.late_count",0]}},"total_late_minutes":{"$sum":{"$ifNull":["$stats.late_minutes",0]}},"leave_count":{"$sum":{"$ifNull":["$stats.leave_count",0]}},"on_duty_count":{"$sum":{"$ifNull":["$stats.on_duty_count",0]}}}},{"$project":{"_id":0,"department":"$_id","headcount":1,"present_days":{"$round":["$present_days",2]},"avg_work_hours":{"$cond":[{"$gt":["$work_count",0]},{"$round":[{"$divide":["$work_sum","$work_count"]},2]},None]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}},{"$sort":{"department":1}}]
    return {"month":month,"items":list(db.employees.aggregate(pipeline,allowDiskUse=True))}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month:str=Query(...,pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    start,end=month_bounds(month)
    pipe=[{"$match":{"date":{"$gte":start.isoformat(),"$lte":end.isoformat()},"late_minutes":{"$gt":0}}},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":"$late_minutes"},"late_count":{"$sum":1}}},{"$lookup":{"from":"employees","localField":"_id","foreignField":"emp_code","as":"emp"}},{"$unwind":"$emp"}]
    if department is not None:pipe.append({"$match":{"emp.department":department}})
    pipe += [{"$sort":{"total_late_minutes":-1,"_id":1}},{"$setWindowFields":{"sortBy":{"total_late_minutes":-1},"output":{"rank":{"$rank":{}}}}},{"$match":{"rank":{"$lte":limit}}},{"$project":{"_id":0,"rank":1,"emp_code":"$_id","name":"$emp.name","department":"$emp.department","total_late_minutes":1,"late_count":1}},{"$sort":{"rank":1,"emp_code":1}}]
    return {"month":month,"items":list(db.attendance_logs.aggregate(pipe,allowDiskUse=True))}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department:str,from_date:str=Query(...,alias="from"),to_date:str=Query(...,alias="to")):
    sd=parse_date(from_date); ed=parse_date(to_date)
    if sd>ed:raise HTTPException(422,"from must be <= to")
    if (ed-sd).days>91:raise HTTPException(422,"date range exceeds 92 days")
    if not db.employees.find_one({"department":department}): raise HTTPException(404,"department not found")
    # Build daily attendance aggregates in MongoDB, densify missing dates, then compute a 7-day moving average.
    pipe = [
        {"$match": {"date": {"$gte": sd.isoformat(), "$lte": ed.isoformat()}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"},
        {"$match": {"emp.department": department}},
        {"$group": {"_id": "$date",
                    "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]},
                                                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}}},
        {"$project": {"_id": 0, "date": {"$dateFromString": {"dateString": "$_id"}},
                      "present_count": 1, "late_count": 1}},
        {"$densify": {"field": "date", "range": {"step": 1, "unit": "day",
                                                  "bounds": [datetime.combine(sd, datetime.min.time()),
                                                             datetime.combine(ed + timedelta(days=1), datetime.min.time())]}}},
        {"$set": {"date": {"$dateToString": {"date": "$date", "format": "%Y-%m-%d"}},
                  "present_count": {"$ifNull": ["$present_count", 0]},
                  "late_count": {"$ifNull": ["$late_count", 0]}}},
        {"$lookup": {"from": "employees", "let": {"day": "$date"},
                     "pipeline": [{"$match": {"$expr": {"$and": [
                         {"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$day"]}]}}},
                                  {"$count": "n"}], "as": "hc"}},
        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]},
                  "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [1, 7]]}]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                          {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]}, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1},
                              "output": {"moving_avg_7d": {"$avg": "$attendance_rate",
                                                           "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
                      "present_count": 1, "late_count": 1, "attendance_rate": 1,
                      "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}}},
        {"$sort": {"date": 1}}
    ]
    # Trend is deliberately computed by MongoDB, including date densification and window stage.
    items=list(db.attendance_logs.aggregate(pipe,allowDiskUse=True))
    # Empty range still needs dates; Mongo $densify cannot seed a completely empty result.
    if not items:
        # Use $documents + lookups to produce dates even when there are no attendance rows.
        dates=[]; cur=sd
        while cur<=ed:
            dates.append(cur.isoformat());cur+=timedelta(days=1)
        for d in dates:
            hc=db.employees.count_documents({"department":department,"joined_on":{"$lte":d}})
            working = parse_date(d).weekday() < 5
            rate = 0.0 if working and hc > 0 else None
            items.append({"date":d,"is_working_day":working,"headcount":hc,"present_count":0,"late_count":0,"attendance_rate":rate,"moving_avg_7d":None})
        # Match $setWindowFields semantics: average only non-null rates in this
        # requested range, using the current row and up to six preceding rows.
        for i, item in enumerate(items):
            window_rates = [row["attendance_rate"] for row in items[max(0, i-6):i+1]
                            if row["attendance_rate"] is not None]
            item["moving_avg_7d"] = round4(sum(window_rates) / len(window_rates)) if window_rates else None
    return {"department":department,"items":items}

def explain_response(endpoint, collection, explain_doc):
    # Keep the raw server explain document while matching the API response contract.
    return {"endpoint": endpoint, "collection": collection, "explain": explain_doc}

@app.get("/admin/explain/{endpoint}")
def explain(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_date:Optional[str]=Query(None,alias="from"),to_date:Optional[str]=Query(None,alias="to"),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if endpoint not in {"attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"}:raise HTTPException(422,"unknown explain endpoint")
    if endpoint=="attendance_list":
        q={}
        if emp_code:q["emp_code"]=emp_code
        if status:q["status"]=status
        if date_from or date_to:
            q["date"]={}
            if date_from:q["date"]["$gte"]=parse_date(date_from).isoformat()
            if date_to:q["date"]["$lte"]=parse_date(date_to).isoformat()
        result = db.command("explain",{"find":"attendance_logs","filter":q,"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size}, verbosity="executionStats")
        return explain_response(endpoint, "attendance_logs", result)
    if not month: raise HTTPException(422,"month is required")
    if endpoint=="employee_monthly":
        if not emp_code:raise HTTPException(422,"emp_code is required")
        result = db.command("explain",{"aggregate":"attendance_logs","pipeline":employee_monthly_pipeline(emp_code,month),"cursor":{}}, verbosity="executionStats")
        return explain_response(endpoint, "attendance_logs", result)
    if endpoint=="department_summary":
        start,end=month_bounds(month); q={"joined_on":{"$lte":end.isoformat()}}
        if department:q["department"]=department
        result = db.command("explain",{"find":"employees","filter":q,"sort":{"department":1}}, verbosity="executionStats")
        return explain_response(endpoint, "employees", result)
    if endpoint=="late_leaderboard":
        start,end=month_bounds(month)
        pipe=[{"$match":{"date":{"$gte":start.isoformat(),"$lte":end.isoformat()},"late_minutes":{"$gt":0}}},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":"$late_minutes"}}}]
        result = db.command("explain",{"aggregate":"attendance_logs","pipeline":pipe,"cursor":{}}, verbosity="executionStats")
        return explain_response(endpoint, "attendance_logs", result)
    if not department or not from_date or not to_date:raise HTTPException(422,"department, from and to are required")
    if parse_date(from_date) > parse_date(to_date):
        raise HTTPException(422, "from must be <= to")
    result = db.command("explain",{"aggregate":"attendance_logs","pipeline":[{"$match":{"date":{"$gte":parse_date(from_date).isoformat(),"$lte":parse_date(to_date).isoformat()}}}],"cursor":{}}, verbosity="executionStats")
    return explain_response(endpoint, "attendance_logs", result)
