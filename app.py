"""
Personal AI Coach - Complete Working Edition
Single file: backend + frontend combined
Run: python app.py  →  open http://localhost:8000
"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import sqlite3, hashlib, json, os, requests, re
from datetime import datetime, date, timedelta

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*","x-api-key"])

DB = "coach.db"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

PREVIOUS_YEAR_PAPERS = [
    {
        "id": "jee-main-2025-22-jan-shift-1",
        "exam": "JEE",
        "title": "JEE Main 2025 · 22 Jan Shift 1",
        "year": 2025,
        "duration_minutes": 180,
        "questions": 75,
        "source": "MathonGo",
        "path": r"C:\Users\venka\Downloads\JEE Main 2025 (22 Jan Shift 1) Previous Year Paper with Answer Keys - MathonGo.pdf",
    },
    {
        "id": "jee-main-2025-22-jan-shift-2",
        "exam": "JEE",
        "title": "JEE Main 2025 · 22 Jan Shift 2",
        "year": 2025,
        "duration_minutes": 180,
        "questions": 75,
        "source": "MathonGo",
        "path": r"C:\Users\venka\Downloads\JEE Main 2025 (22 Jan Shift 2) Previous Year Paper with Answer Keys - MathonGo.pdf",
    },
    {
        "id": "neet-2024-key-3577818",
        "exam": "NEET",
        "title": "NEET 2024 · key_3577818",
        "year": 2024,
        "duration_minutes": 200,
        "questions": 200,
        "source": "PDF Upload",
        "path": r"C:\Users\venka\Downloads\key_3577818_2024-05-07 08_07_55 +0000.pdf",
    },
    {
        "id": "neet-2024-key-5057800",
        "exam": "NEET",
        "title": "NEET 2024 · key_5057800",
        "year": 2024,
        "duration_minutes": 200,
        "questions": 180,
        "source": "PDF Upload",
        "path": r"C:\Users\venka\Downloads\key_5057800_2025-02-17 08_56_28 +0000.pdf",
    },
]
PAPER_INDEX = {paper["id"]: paper for paper in PREVIOUS_YEAR_PAPERS}

# ─── GROQ ──────────────────────────────────────────────────────────
def groq(prompt, system="", key="", tokens=2048):
    k = key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No GROQ_API_KEY - set: $env:GROQ_API_KEY=gsk_...")
    msgs = []
    if system: msgs.append({"role":"system","content":system})
    msgs.append({"role":"user","content":prompt})
    try:
        r = requests.post(GROQ_URL,
            headers={"Authorization":f"Bearer {k}","Content-Type":"application/json"},
            json={"model":MODEL,"messages":msgs,"max_tokens":tokens,"temperature":0.7},
            timeout=60)
    except requests.exceptions.ConnectionError:
        raise HTTPException(503,"Cannot reach Groq - check internet")
    except requests.exceptions.Timeout:
        raise HTTPException(504,"Groq timed out - try again")
    if r.status_code==401: raise HTTPException(401,"Invalid Groq key")
    if r.status_code==429: raise HTTPException(429,"Rate limit - wait a moment")
    if not r.ok: raise HTTPException(500,f"Groq error {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]

def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m: text = m.group(1).strip()
    s, e = text.find("["), text.rfind("]")
    if s==-1 or e==-1: raise ValueError("No JSON array in response")
    return json.loads(text[s:e+1])

def getdb():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def initdb():
    c = getdb()
    # Core tables (compatible with old schema)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        exam_type TEXT);
    CREATE TABLE IF NOT EXISTS quiz_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, subject TEXT, topic TEXT,
        started_at TEXT, score INTEGER DEFAULT 0,
        total INTEGER DEFAULT 0, student_level TEXT DEFAULT 'mid');
    CREATE TABLE IF NOT EXISTS quiz_responses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER, question TEXT,
        correct_answer TEXT, user_answer TEXT,
        is_correct INTEGER, response_time_ms INTEGER,
        question_index INTEGER);
    CREATE TABLE IF NOT EXISTS study_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, date TEXT, hour INTEGER,
        score_avg REAL DEFAULT 0,
        fatigue_detected INTEGER DEFAULT 0, session_type TEXT);
    CREATE TABLE IF NOT EXISTS spaced_rep(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, subject TEXT, topic TEXT,
        box INTEGER DEFAULT 1, next_review TEXT,
        wrong_count INTEGER DEFAULT 0, right_count INTEGER DEFAULT 0,
        last_seen TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS pyq(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam TEXT, subject TEXT, topic TEXT, year INTEGER,
        question TEXT, options TEXT, correct TEXT, explanation TEXT);
    CREATE TABLE IF NOT EXISTS self_report(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, session_id INTEGER,
        feeling TEXT, created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS fatigue_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, session_id INTEGER,
        auto_score INTEGER DEFAULT 0, self_score INTEGER DEFAULT 0,
        blink_rate REAL DEFAULT 0, eye_open REAL DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')));
    """)
    c.commit()
    # Safe migrations for existing DBs
    for sql in [
        "ALTER TABLE quiz_sessions ADD COLUMN mode TEXT DEFAULT 'normal'",
        "ALTER TABLE quiz_sessions ADD COLUMN negative_marks REAL DEFAULT 0",
        "ALTER TABLE quiz_responses ADD COLUMN user_id INTEGER DEFAULT 0",
        "ALTER TABLE quiz_responses ADD COLUMN subject TEXT DEFAULT ''",
        "ALTER TABLE quiz_responses ADD COLUMN topic TEXT DEFAULT ''",
    ]:
        try: c.execute(sql); c.commit()
        except: pass
    seed_pyq(c)
    c.close()

def seed_pyq(c):
    if c.execute("SELECT COUNT(*) as n FROM pyq").fetchone()["n"] > 0: return
    pyqs = [
        ("NEET","Physics","Motion & Laws",2023,"A body of mass 5kg moves at 3 m/s. Kinetic energy?",
         '["A) 22.5 J","B) 15 J","C) 45 J","D) 7.5 J"]',"A) 22.5 J","KE=0.5xmxv^2=0.5x5x9=22.5 J"),
        ("NEET","Physics","Motion & Laws",2022,"Ball thrown up at 20m/s. Max height? (g=10)",
         '["A) 10 m","B) 20 m","C) 40 m","D) 5 m"]',"B) 20 m","h=v^2/2g=400/20=20 m"),
        ("NEET","Chemistry","Atomic Structure",2023,"Number of orbitals in n=3 shell?",
         '["A) 3","B) 6","C) 9","D) 12"]',"C) 9","n=3 has s,p,d: 1+3+5=9 orbitals"),
        ("NEET","Chemistry","Atomic Structure",2022,"Which quantum number determines orbital shape?",
         '["A) Principal","B) Azimuthal","C) Magnetic","D) Spin"]',"B) Azimuthal","Azimuthal (l) determines shape"),
        ("NEET","Biology","Cell Biology",2023,"Powerhouse of the cell?",
         '["A) Nucleus","B) Ribosome","C) Mitochondria","D) Golgi"]',"C) Mitochondria","Mitochondria produce ATP"),
        ("NEET","Biology","Genetics",2022,"DNA replication is?",
         '["A) Conservative","B) Semi-conservative","C) Dispersive","D) Non-conservative"]',"B) Semi-conservative","Meselson-Stahl proved semi-conservative"),
        ("JEE","Physics","Kinematics",2023,"Particle in circle R. Displacement after half revolution?",
         '["A) piR","B) 2R","C) R","D) 2piR"]',"B) 2R","Displacement=diameter=2R"),
        ("JEE","Mathematics","Integration",2022,"Integral of (2x+1)dx?",
         '["A) x^2+x+C","B) x^2+C","C) 2x+C","D) x+C"]',"A) x^2+x+C","Integrate term by term"),
        ("JEE","Chemistry","Mole Concept",2023,"Moles in 44g CO2 (MW=44)?",
         '["A) 0.5","B) 1","C) 2","D) 44"]',"B) 1","moles=mass/MW=44/44=1"),
        ("JEE","Mathematics","Trigonometry",2022,"sin^2+cos^2=?",
         '["A) 0","B) 2","C) 1","D) sincos"]',"C) 1","Pythagorean identity"),
    ]
    for p in pyqs:
        c.execute("INSERT INTO pyq(exam,subject,topic,year,question,options,correct,explanation) VALUES(?,?,?,?,?,?,?,?)",p)
    c.commit()

initdb()

def hp(p): return hashlib.sha256(p.encode()).hexdigest()

SUBJ = {
    "NEET":{"Physics":["Motion & Laws","Work Energy Power","Gravitation","Thermodynamics","Waves","Ray Optics","Electrostatics","Current Electricity","Magnetic Effects","Atoms & Nuclei","Semiconductors"],"Chemistry":["Atomic Structure","Chemical Bonding","Thermochemistry","Equilibrium","Electrochemistry","Organic Basics","Hydrocarbons","Biomolecules","Polymers","Coordination Compounds","p-Block"],"Biology":["Cell Biology","Cell Division","Genetics","Molecular Inheritance","Evolution","Digestion","Respiration","Excretion","Nervous System","Reproduction","Plant Physiology","Ecology","Biotechnology"]},
    "JEE":{"Physics":["Kinematics","Laws of Motion","Work Energy","Rotation","Gravitation","SHM","Waves","Electrostatics","Current Electricity","EMI","Optics","Modern Physics"],"Chemistry":["Mole Concept","Chemical Bonding","States of Matter","Thermodynamics","Kinetics","Electrochemistry","Organic Reactions","Stereochemistry","Coordination","d-f Block","p-Block"],"Mathematics":["Sets","Trigonometry","Complex Numbers","Quadratics","Sequences","Binomial","Coordinate Geometry","3D Geometry","Vectors","Limits","Derivatives","Integration","Differential Equations","Probability","Matrices"]}
}

# ─── MODELS ───────────────────────────────────────────────────────
class LoginReq(BaseModel): username:str; password:str
class ExamReq(BaseModel): user_id:int; exam_type:str
class QuizReq(BaseModel): user_id:int; subject:str; topic:str; level:str="mid"; exam:str="NEET"; mode:str="normal"
class AnswerReq(BaseModel): session_id:int; user_id:int; subject:str; topic:str; question:str; correct:str; answer:str; ms:int; idx:int; is_correct:bool
class FatReq(BaseModel): session_id:int; user_id:int; times:List[int]; streak:int; total:int; ks:int=0; ss:int=0; bs:int=0; eo:float=1.0; sf:str=""
class ChatReq(BaseModel): user_id:int; message:str; context:str=""; wrong_q:str=""; wrong_a:str=""
class SchedReq(BaseModel): user_id:int; exam:str="NEET"
class SRReq(BaseModel): user_id:int; subject:str; topic:str; passed:bool

# ─── AUTH ─────────────────────────────────────────────────────────
@app.post("/api/signup")
def signup(r:LoginReq):
    c=getdb()
    try:
        c.execute("INSERT INTO users(username,password) VALUES(?,?)",(r.username,hp(r.password)))
        c.commit()
        u=c.execute("SELECT * FROM users WHERE username=?",(r.username,)).fetchone()
        return {"id":u["id"],"username":u["username"]}
    except sqlite3.IntegrityError: raise HTTPException(400,"Username taken")
    finally: c.close()

@app.post("/api/login")
def login(r:LoginReq):
    c=getdb()
    u=c.execute("SELECT * FROM users WHERE username=? AND password=?",(r.username,hp(r.password))).fetchone()
    c.close()
    if not u: raise HTTPException(401,"Wrong username or password")
    return {"id":u["id"],"username":u["username"],"exam":u["exam_type"]}

@app.post("/api/exam")
def set_exam(r:ExamReq):
    c=getdb(); c.execute("UPDATE users SET exam_type=? WHERE id=?",(r.exam_type,r.user_id)); c.commit(); c.close()
    return {"ok":True}

# ─── QUIZ ─────────────────────────────────────────────────────────
@app.post("/api/quiz")
def quiz(r:QuizReq, x_api_key:Optional[str]=Header(default=None)):
    # PYQ mode
    if r.mode=="pyq":
        c=getdb()
        rows=c.execute("SELECT * FROM pyq WHERE exam=? AND subject=? ORDER BY RANDOM() LIMIT 5",(r.exam.upper(),r.subject)).fetchall()
        c.close()
        if not rows: raise HTTPException(404,f"No PYQs for {r.exam} {r.subject}")
        qs=[{"question":row["question"],"options":json.loads(row["options"]),"correct":row["correct"],"explanation":row["explanation"],"visual_aid":"","formula":"","diagram":"","year":row["year"]} for row in rows]
        c=getdb()
        sid=c.execute("INSERT INTO quiz_sessions(user_id,subject,topic,started_at,student_level,mode) VALUES(?,?,?,?,?,?)",(r.user_id,r.subject,r.topic,datetime.now().isoformat(),r.level,"pyq")).lastrowid
        c.commit(); c.close()
        return {"sid":sid,"questions":qs,"level":r.level}

    k = x_api_key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No GROQ_API_KEY")

    lvl_map={"bright":"challenging application/analysis requiring deep understanding","mid":"moderate difficulty standard application","low":"foundational basic concepts simple language"}
    ldesc=lvl_map.get(r.level,"moderate difficulty")
    vi='visual_aid: real-life analogy 1 sentence, formula: key formula, diagram: helpful diagram description' if r.level=="low" else 'visual_aid: one analogy sentence' if r.level=="mid" else 'visual_aid: ""'

    prompt=f"""You are a {r.exam} exam coach. Generate exactly 5 MCQ questions for {r.subject} - {r.topic} at {ldesc}.
{vi}
Return ONLY a valid JSON array. No text outside the array:
[{{"question":"?","options":["A) ","B) ","C) ","D) "],"correct":"A) ","explanation":"","visual_aid":"","formula":"","diagram":""}}]
Rules: correct must exactly match one option. All 4 options plausible. Follow {r.exam} syllabus."""

    try:
        raw=groq(prompt,key=k,tokens=2500)
        qs=parse_json(raw)
        if not isinstance(qs,list) or not qs: raise ValueError("Empty")
        for q2 in qs:
            q2.setdefault("explanation",""); q2.setdefault("visual_aid","")
            q2.setdefault("formula",""); q2.setdefault("diagram","")
    except HTTPException: raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(500,f"Quiz failed: {e}")

    c=getdb(); now=datetime.now()
    sid=c.execute("INSERT INTO quiz_sessions(user_id,subject,topic,started_at,student_level,mode) VALUES(?,?,?,?,?,?)",(r.user_id,r.subject,r.topic,now.isoformat(),r.level,r.mode)).lastrowid
    c.execute("INSERT INTO study_sessions(user_id,date,hour,session_type) VALUES(?,?,?,?)",(r.user_id,now.date().isoformat(),now.hour,r.subject))
    c.commit(); c.close()
    return {"sid":sid,"questions":qs,"level":r.level}

# ─── ADAPTIVE LEVEL ───────────────────────────────────────────────
@app.get("/api/adapt/{sid}/{cs}/{ws}")
def adapt(sid:int, cs:int, ws:int):
    c=getdb(); s=c.execute("SELECT student_level FROM quiz_sessions WHERE id=?",(sid,)).fetchone(); c.close()
    cur=s["student_level"] if s else "mid"
    new=cur; reason="No change"
    if cs>=2:
        if cur=="low": new="mid"; reason="2 correct → Mid level"
        elif cur=="mid": new="bright"; reason="2 correct → Bright level"
        else: reason="Already at Bright!"
    elif ws>=2:
        if cur=="bright": new="mid"; reason="Adjusted to Mid"
        elif cur=="mid": new="low"; reason="Adjusted to Low - visual help enabled"
        else: reason="Stay at foundational level"
    if new!=cur:
        c=getdb(); c.execute("UPDATE quiz_sessions SET student_level=? WHERE id=?",(new,sid)); c.commit(); c.close()
    return {"level":new,"changed":new!=cur,"reason":reason}

# ─── TEACHING CARD ────────────────────────────────────────────────
@app.get("/api/teach")
def teach(subject:str, topic:str, exam:str="NEET", x_api_key:Optional[str]=Header(default=None)):
    k=x_api_key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No API key")
    prompt=f"""Create a visual teaching card for {exam} {subject} - {topic} for a struggling student.
Return ONLY a JSON object:
{{"title":"{topic}","analogy":"Real-life analogy 1-2 sentences","key_formula":"Most important formula","diagram":"Simple diagram description","remember_tip":"Memory trick","example":"One simple example"}}"""
    try:
        text=groq(prompt,key=k,tokens=500)
        text=text.strip()
        m=re.search(r"```(?:json)?\s*([\s\S]*?)```",text)
        if m: text=m.group(1).strip()
        s,e=text.find("{"),text.rfind("}")
        return json.loads(text[s:e+1])
    except:
        return {"title":topic,"analogy":f"Think of {topic} as something you see daily.","key_formula":"Review your notes.","diagram":"Draw a simple diagram.","remember_tip":"Break into small steps.","example":"Try a simple numerical first."}

# ─── ANSWER ───────────────────────────────────────────────────────
@app.post("/api/answer")
def answer(r:AnswerReq):
    ok=1 if r.is_correct else 0
    c=getdb()
    c.execute("INSERT INTO quiz_responses(session_id,user_id,subject,topic,question,correct_answer,user_answer,is_correct,response_time_ms,question_index) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (r.session_id,r.user_id,r.subject,r.topic,r.question,r.correct,r.answer,ok,r.ms,r.idx))
    if ok: c.execute("UPDATE quiz_sessions SET score=score+1,total=total+1 WHERE id=?",(r.session_id,))
    else:
        c.execute("UPDATE quiz_sessions SET total=total+1 WHERE id=?",(r.session_id,))
        # Spaced rep
        ex=c.execute("SELECT * FROM spaced_rep WHERE user_id=? AND topic=?",(r.user_id,r.topic)).fetchone()
        ivs=[1,1,3,7,14]
        if ex:
            b=max(1,ex["box"]-1)
            c.execute("UPDATE spaced_rep SET box=?,next_review=?,wrong_count=wrong_count+1,last_seen=? WHERE id=?",
                (b,(date.today()+timedelta(days=ivs[b-1])).isoformat(),datetime.now().isoformat(),ex["id"]))
        else:
            c.execute("INSERT INTO spaced_rep(user_id,subject,topic,box,next_review,wrong_count) VALUES(?,?,?,1,?,1)",
                (r.user_id,r.subject,r.topic,(date.today()+timedelta(days=1)).isoformat()))
    c.commit()
    s=c.execute("SELECT * FROM quiz_sessions WHERE id=?",(r.session_id,)).fetchone()
    if s and s["total"]>0:
        now=datetime.now()
        c.execute("UPDATE study_sessions SET score_avg=? WHERE user_id=? AND date=? AND hour=?",
            (s["score"]/s["total"]*100,s["user_id"],now.date().isoformat(),now.hour))
        c.commit()
    c.close()
    return {"ok":True,"correct":bool(ok)}

# ─── FATIGUE ──────────────────────────────────────────────────────
@app.post("/api/fatigue")
def fatigue(r:FatReq):
    sc=0; sigs={}; t=r.times
    if len(t)>=3:
        if sum(t[-3:])/3>sum(t[:3])/3*1.5: sc+=3; sigs["Response Time"]="slowing"
        elif sum(t[-3:])/3>sum(t[:3])/3*1.2: sc+=1
    if r.streak>=3: sc+=3; sigs["Error Streak"]=f"{r.streak} wrong"
    elif r.streak>=2: sc+=1
    if t and max(t)>30000: sc+=2; sigs["Slow Response"]=">30s"
    sc+=min(3,r.ks); sigs["Typing"]=r.ks
    sc+=min(2,r.ss); sigs["Scroll"]=r.ss
    sc+=min(3,r.bs); sigs["Eye Blink"]=r.bs
    if r.eo<0.5: sc+=3; sigs["Eye Drooping"]=True
    elif r.eo<0.7: sc+=1
    self_sc={"sharp":0,"okay":1,"tired":3}.get(r.sf.lower(),0)
    sc+=self_sc
    if r.sf: sigs["Self Report"]=r.sf
    tired=sc>=5
    c=getdb()
    c.execute("INSERT INTO fatigue_log(user_id,session_id,auto_score,self_score,blink_rate,eye_open) VALUES(?,?,?,?,?,?)",
        (r.user_id,r.session_id,sc,self_sc,r.bs,r.eo))
    if tired:
        c.execute("UPDATE study_sessions SET fatigue_detected=1 WHERE user_id=? AND date=? AND hour=?",
            (r.user_id,date.today().isoformat(),datetime.now().hour))
    c.commit(); c.close()
    return {"tired":tired,"score":sc,"signals":sigs}

# ─── SELF REPORT ──────────────────────────────────────────────────
@app.post("/api/self-report")
def self_report(user_id:int, session_id:int, feeling:str):
    c=getdb(); c.execute("INSERT INTO self_report(user_id,session_id,feeling) VALUES(?,?,?)",(user_id,session_id,feeling)); c.commit(); c.close()
    return {"ok":True}

# ─── SPACED REP ───────────────────────────────────────────────────
@app.get("/api/spaced-rep/{uid}")
def spaced_rep(uid:int):
    c=getdb()
    due=c.execute("SELECT * FROM spaced_rep WHERE user_id=? AND next_review<=? ORDER BY box ASC LIMIT 10",(uid,date.today().isoformat())).fetchall()
    c.close()
    return {"due":[{"subject":r["subject"],"topic":r["topic"],"box":r["box"],"wrong":r["wrong_count"],"next":r["next_review"]} for r in due],"count":len(due)}

@app.post("/api/spaced-rep/update")
def sr_update(r:SRReq):
    c=getdb(); row=c.execute("SELECT * FROM spaced_rep WHERE user_id=? AND topic=?",(r.user_id,r.topic)).fetchone()
    if not row: c.close(); return {"ok":False}
    ivs=[1,1,3,7,14,30]
    if r.passed:
        nb=min(5,row["box"]+1)
        c.execute("UPDATE spaced_rep SET box=?,right_count=right_count+1,next_review=?,last_seen=? WHERE id=?",
            (nb,(date.today()+timedelta(days=ivs[nb-1])).isoformat(),datetime.now().isoformat(),row["id"]))
    else:
        c.execute("UPDATE spaced_rep SET box=1,wrong_count=wrong_count+1,next_review=?,last_seen=? WHERE id=?",
            ((date.today()+timedelta(days=1)).isoformat(),datetime.now().isoformat(),row["id"]))
    c.commit(); c.close()
    return {"ok":True}

# ─── WEAK TOPICS ──────────────────────────────────────────────────
@app.get("/api/weak-topics/{uid}")
def weak_topics(uid:int):
    c=getdb()
    rows=c.execute("SELECT topic,subject,AVG(CAST(is_correct AS REAL))*100 as acc,COUNT(*) as n FROM quiz_responses WHERE user_id=? GROUP BY topic HAVING n>=3 ORDER BY acc ASC",(uid,)).fetchall()
    c.close()
    return {"weak":[{"topic":r["topic"],"subject":r["subject"],"acc":round(r["acc"],1)} for r in rows if r["acc"]<50],
            "strong":[{"topic":r["topic"],"subject":r["subject"],"acc":round(r["acc"],1)} for r in rows if r["acc"]>=75]}

# ─── STATS ────────────────────────────────────────────────────────
@app.get("/api/stats/{uid}")
def stats(uid:int):
    c=getdb()
    hourly=c.execute("SELECT hour,AVG(score_avg) as avg,COUNT(*) as n FROM study_sessions WHERE user_id=? GROUP BY hour ORDER BY hour",(uid,)).fetchall()
    subjects=c.execute("SELECT subject,AVG(CAST(score AS REAL)/CASE WHEN total=0 THEN 1 ELSE total END)*100 as ap,COUNT(*) as n,student_level FROM quiz_sessions WHERE user_id=? AND total>0 GROUP BY subject",(uid,)).fetchall()
    recent=c.execute("SELECT subject,topic,student_level,score,total,mode FROM quiz_sessions WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()
    weak_r=c.execute("SELECT topic,subject,AVG(CAST(is_correct AS REAL))*100 as acc,COUNT(*) as n FROM quiz_responses WHERE user_id=? GROUP BY topic HAVING n>=3 ORDER BY acc ASC",(uid,)).fetchall()
    sr_due=c.execute("SELECT COUNT(*) as n FROM spaced_rep WHERE user_id=? AND next_review<=?",(uid,date.today().isoformat())).fetchone()
    fat=c.execute("SELECT AVG(auto_score) as avg FROM fatigue_log WHERE user_id=?",(uid,)).fetchone()
    c.close()
    def cl(a): return "peak" if (a or 0)>=70 else "mid" if (a or 0)>=45 else "low"
    hd=[{"h":r["hour"],"avg":round(r["avg"] or 0,1),"type":cl(r["avg"])} for r in hourly]
    plan={}
    for t,rec in [("peak","Study hard topics — new concepts"),("mid","Practice MCQs and papers"),("low","Light revision — formulas")]:
        hrs=[x["h"] for x in hd if x["type"]==t]
        if hrs: plan[t]={"hours":hrs,"tip":rec}
    return {"hourly":hd,"subjects":[{"s":r["subject"],"avg":round(r["ap"] or 0,1),"n":r["n"],"lv":r["student_level"]} for r in subjects],
            "recent":[{"s":r["subject"],"t":r["topic"],"lv":r["student_level"],"sc":r["score"],"tot":r["total"],"mode":r["mode"] if r["mode"] else "normal"} for r in recent],
            "plan":plan,"weak_topics":[{"topic":r["topic"],"subject":r["subject"],"acc":round(r["acc"],1)} for r in weak_r if r["acc"]<50],
            "strong_topics":[{"topic":r["topic"],"subject":r["subject"],"acc":round(r["acc"],1)} for r in weak_r if r["acc"]>=75],
            "spaced_rep_due":sr_due["n"] if sr_due else 0,"avg_fatigue":round(fat["avg"] or 0,1) if fat else 0}

@app.get("/api/papers")
def list_papers(exam:Optional[str]=None):
    exam_key=(exam or "").upper().strip()
    papers=[]
    for paper in PREVIOUS_YEAR_PAPERS:
        if exam_key and paper["exam"]!=exam_key:
            continue
        if not os.path.exists(paper["path"]):
            continue
        papers.append({k:v for k,v in paper.items() if k!="path"})
    return {"papers":papers}

@app.get("/api/papers/{paper_id}")
def get_paper(paper_id:str):
    paper=PAPER_INDEX.get(paper_id)
    if not paper:
        raise HTTPException(404,"Paper not found")
    if not os.path.exists(paper["path"]):
        raise HTTPException(404,"Paper file is missing")
    return FileResponse(
        paper["path"],
        media_type="application/pdf",
        filename=os.path.basename(paper["path"]),
        content_disposition_type="inline",
    )

# ─── LEVEL ────────────────────────────────────────────────────────
@app.get("/api/level/{sid}")
def level(sid:int):
    c=getdb(); s=c.execute("SELECT * FROM quiz_sessions WHERE id=?",(sid,)).fetchone(); c.close()
    if not s: return {"level":"mid"}
    pct=s["score"]/max(1,s["total"])*100; lv="bright" if pct>=75 else "mid" if pct>=50 else "low"
    c=getdb(); c.execute("UPDATE quiz_sessions SET student_level=? WHERE id=?",(lv,sid)); c.commit(); c.close()
    return {"level":lv,"pct":round(pct,1)}

# ─── SCHEDULE ─────────────────────────────────────────────────────
@app.post("/api/schedule")
def schedule(r:SchedReq, x_api_key:Optional[str]=Header(default=None)):
    k=x_api_key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No API key")
    c=getdb()
    h=c.execute("SELECT hour,AVG(score_avg) as avg FROM study_sessions WHERE user_id=? GROUP BY hour ORDER BY avg DESC",(r.user_id,)).fetchall()
    w=c.execute("SELECT topic,subject,AVG(CAST(is_correct AS REAL))*100 as acc FROM quiz_responses WHERE user_id=? GROUP BY topic HAVING COUNT(*)>=3 ORDER BY acc ASC LIMIT 5",(r.user_id,)).fetchall()
    c.close()
    ph=[x["hour"] for x in h[:3]]; wl=[f"{x['subject']}-{x['topic']}({round(x['acc'])}%)" for x in w]
    prompt=f"""Create a 7-day study schedule for a {r.exam} student.
Peak hours:{ph if ph else "morning"}. Weak topics:{wl if wl else ["balance all"]}.
Subjects:{list(SUBJ.get(r.exam,SUBJ["NEET"]).keys())}
Return ONLY JSON array of 7 days:
[{{"day":"Monday","slots":[{{"time":"8:00-9:00","type":"peak","subject":"Physics","topic":"Thermodynamics","activity":"New concepts"}}]}}]
2-4 slots per day."""
    try:
        text=groq(prompt,key=k,tokens=2000); text=text.strip()
        m=re.search(r"```(?:json)?\s*([\s\S]*?)```",text)
        if m: text=m.group(1).strip()
        s,e=text.find("["),text.rfind("]")
        return {"schedule":json.loads(text[s:e+1]),"peak_hours":ph,"weak_topics":wl}
    except Exception as ex: raise HTTPException(500,f"Schedule failed: {ex}")

# ─── CHAT ─────────────────────────────────────────────────────────
@app.post("/api/chat")
def chat(r:ChatReq, x_api_key:Optional[str]=Header(default=None)):
    k=x_api_key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No API key")
    c=getdb()
    u=c.execute("SELECT * FROM users WHERE id=?",(r.user_id,)).fetchone()
    sess=c.execute("SELECT subject,score,total,student_level FROM quiz_sessions WHERE user_id=? ORDER BY id DESC LIMIT 5",(r.user_id,)).fetchall()
    weak=c.execute("SELECT topic,AVG(CAST(is_correct AS REAL))*100 as acc FROM quiz_responses WHERE user_id=? GROUP BY topic HAVING COUNT(*)>=3 ORDER BY acc ASC LIMIT 3",(r.user_id,)).fetchall()
    c.close()
    perf="\n".join(f"  {s['subject']}: {round(s['score']/max(1,s['total'])*100)}% ({s['student_level']})" for s in sess) or "  No data"
    wk=", ".join(x["topic"] for x in weak) or "none yet"
    exam=u["exam_type"] if u and u["exam_type"] else "NEET/JEE"
    uname=u["username"] if u else "student"
    sys=f"""You are an expert {exam} AI Study Coach for {uname}.
Performance:\n{perf}\nWeak topics: {wk}\nContext: {r.context}"""
    msg=r.message
    if r.wrong_q:
        sys+=f"\n\nStudent just got WRONG:\nQ: {r.wrong_q}\nCorrect: {r.wrong_a}\nExplain step-by-step why the answer is correct."
    sys+="\n\nBe encouraging, specific. Max 150 words. Give step-by-step when asked."
    try:
        return {"reply":groq(msg,system=sys,key=k,tokens=400)}
    except HTTPException: raise
    except Exception as ex: raise HTTPException(500,f"Chat failed: {ex}")

# ─── UPLOAD ───────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload(file:UploadFile=File(...), exam:str="NEET", subject:str="General", x_api_key:Optional[str]=Header(default=None)):
    k=x_api_key or os.environ.get("GROQ_API_KEY","")
    if not k: raise HTTPException(400,"No API key")
    text=(await file.read()).decode("utf-8",errors="ignore")[:4000]
    prompt=f"Generate 5 MCQs from this {exam} {subject} material.\nMaterial: {text}\nReturn ONLY JSON array:[{{\"question\":\"\",\"options\":[\"A) \",\"B) \",\"C) \",\"D) \"],\"correct\":\"A) \",\"explanation\":\"\",\"visual_aid\":\"\",\"formula\":\"\",\"diagram\":\"\"}}]"
    try: return {"questions":parse_json(groq(prompt,key=k,tokens=2000))}
    except HTTPException: raise
    except Exception as ex: raise HTTPException(500,str(ex))

# ─── RECOVERY ─────────────────────────────────────────────────────
@app.get("/api/recovery")
def recovery():
    return {"options":[{"title":"Meditation","icon":"🧘","url":"https://www.youtube.com/embed/inpok4MKVLM"},{"title":"Box Breathing","icon":"🌬️","url":"https://www.youtube.com/embed/uxayUBd6T7M"},{"title":"Stretch","icon":"🤸","url":"https://www.youtube.com/embed/tAUf7aajBWE"}]}

@app.get("/favicon.ico")
def fav(): return ""

# ─── SERVE FRONTEND ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root(): return HTMLResponse(content=HTML)


HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>AI Coach — NEET & JEE</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Inter,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
.H{display:none!important}
nav{background:#1a1d27;border-bottom:1px solid #2d3148;padding:0 18px;height:54px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:9}
.logo{font-weight:800;color:#7c6fff}.nls{display:flex;gap:2px}
.nl{background:none;border:none;color:#64748b;padding:6px 11px;border-radius:7px;cursor:pointer;font:600 .78rem Inter;transition:.15s}
.nl:hover,.nl.on{background:#2d3148;color:#e2e8f0}
.nr{display:flex;align-items:center;gap:7px;font-size:.78rem;color:#64748b}
.av{width:26px;height:26px;border-radius:50%;background:#7c6fff;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:.68rem}
.wrap{max-width:900px;margin:0 auto;padding:20px}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 18px;border-radius:9px;border:none;cursor:pointer;font:600 .85rem Inter;transition:.15s;gap:6px}
.btn:disabled{opacity:.4;pointer-events:none}
.bp{background:#7c6fff;color:#fff}.bp:hover{background:#6a5de8}
.bs{background:#1e2235;border:1px solid #2d3148;color:#e2e8f0}.bs:hover{border-color:#7c6fff}
.bg{background:#10b981;color:#fff}.br{background:#f87171;color:#fff}.bw{width:100%}
.card{background:#1a1d27;border:1px solid #2d3148;border-radius:12px;padding:18px}
.f{margin-bottom:13px}
.f label{display:block;font-size:.7rem;font-weight:700;color:#64748b;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em}
.f input,.f select,.f textarea{width:100%;padding:9px 12px;background:#1e2235;border:1.5px solid #2d3148;border-radius:8px;color:#e2e8f0;font:inherit;font-size:.86rem}
.f input:focus,.f select:focus,.f textarea:focus{outline:none;border-color:#7c6fff}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.g4{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:11px}
.tile{padding:18px 13px;text-align:center;cursor:pointer;border:1.5px solid #2d3148;border-radius:11px;background:#1a1d27;transition:.18s}
.tile:hover,.tile.on{border-color:#7c6fff;background:rgba(124,111,255,.07)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.chip{padding:5px 12px;background:#1e2235;border:1.5px solid #2d3148;border-radius:20px;cursor:pointer;font-size:.8rem;font-weight:500;transition:.15s}
.chip:hover,.chip.on{background:#7c6fff;border-color:#7c6fff;color:#fff}
.hud{display:flex;align-items:center;gap:10px;padding:11px 15px;background:#1a1d27;border:1px solid #2d3148;border-radius:11px;margin-bottom:14px;flex-wrap:wrap}
.ht{font:700 .9rem monospace;background:#1e2235;padding:4px 11px;border-radius:7px;min-width:50px;text-align:center}
.pb{background:#1e2235;border-radius:3px;height:5px;overflow:hidden;flex:1;min-width:80px}.pbi{height:100%;background:#7c6fff;transition:width .4s;border-radius:3px}
.qcard{background:#1a1d27;border:1px solid #2d3148;border-radius:13px;padding:22px}
.qn{font:.68rem/1 monospace;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.qt{font-size:1rem;font-weight:500;line-height:1.65;margin-bottom:16px}
.tcard{background:linear-gradient(135deg,rgba(124,111,255,.08),rgba(16,185,129,.04));border:1px solid rgba(124,111,255,.2);border-radius:11px;padding:16px;margin-bottom:16px}
.tgrid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:9px}
.ti{background:#1e2235;border-radius:8px;padding:10px}.ti-l{font-size:.63rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748b;margin-bottom:3px}.ti-v{font-size:.82rem;line-height:1.5}
.formula{background:#1a1d27;border:1px solid rgba(124,111,255,.25);border-radius:7px;padding:9px;font-family:monospace;font-size:.88rem;color:#7c6fff;text-align:center;margin-top:8px}
.opts{display:flex;flex-direction:column;gap:7px}
.opt{display:flex;align-items:flex-start;gap:9px;padding:11px 14px;background:#1e2235;border:1.5px solid #2d3148;border-radius:9px;cursor:pointer;font-size:.85rem;text-align:left;color:#e2e8f0;transition:.15s;width:100%;font-family:inherit}
.opt:hover:not([disabled]){border-color:#7c6fff;background:rgba(124,111,255,.07)}
.opt.R{border-color:#10b981!important;background:rgba(16,185,129,.07)!important;color:#34d399}
.opt.W{border-color:#f87171!important;background:rgba(248,113,113,.07)!important;color:#f87171}
.opt:disabled{cursor:default}
.ok{min-width:23px;height:23px;border-radius:5px;background:#2d3148;display:flex;align-items:center;justify-content:center;font:.68rem/1 monospace;font-weight:700;flex-shrink:0}
.exp{background:#1e2235;border-left:3px solid #10b981;border-radius:0 8px 8px 0;padding:10px 13px;margin-top:12px;font-size:.8rem;line-height:1.6;color:#94a3b8}
.lbadge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:20px;font-size:.7rem;font-weight:700;text-transform:uppercase}
.lb-b{background:rgba(124,111,255,.15);color:#a5b4fc;border:1px solid rgba(124,111,255,.25)}
.lb-m{background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid rgba(245,158,11,.25)}
.lb-l{background:rgba(248,113,113,.12);color:#f87171;border:1px solid rgba(248,113,113,.25)}
.lvt{position:fixed;top:68px;left:50%;transform:translateX(-50%);background:#1a1d27;border:1px solid #7c6fff;border-radius:12px;padding:11px 20px;font-size:.84rem;font-weight:600;z-index:500;opacity:0;transition:all .35s;white-space:nowrap;pointer-events:none}
.lvt.on{opacity:1}
.fd{width:7px;height:7px;border-radius:50%;background:#2d3148;transition:background .3s;display:inline-block;margin:0 2px}
.fd.g{background:#10b981}.fd.y{background:#f59e0b}.fd.r{background:#f87171}
.rpct{font:800 3rem/1 monospace;margin:10px 0}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:300;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}
.mbox{background:#1a1d27;border:1px solid #2d3148;border-radius:17px;padding:26px;max-width:390px;width:100%;text-align:center}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.86);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}
.ovb{background:#1a1d27;border:1px solid #2d3148;border-radius:17px;padding:26px;max-width:460px;width:100%;text-align:center}
.vov{position:fixed;inset:0;background:rgba(0,0,0,.97);z-index:400;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:20px}
.vtm{font:800 2.4rem/1 monospace;color:#7c6fff}.vfr{width:100%;max-width:520px;aspect-ratio:16/9;border-radius:10px;border:none}
.etimer{font:800 1.3rem/1 monospace;color:#f87171}
.sr-c{border-left:3px solid;padding:11px;border-radius:0 9px 9px 0;background:#1e2235;margin-bottom:7px}
.ss{display:flex;gap:10px;padding:9px 12px;background:#1e2235;border-radius:9px;margin-bottom:5px;align-items:center}
.st{font:700 .76rem monospace;min-width:88px;color:#a5b4fc}.sd{width:8px;height:8px;border-radius:50%;flex-shrink:0}
#WCV{position:fixed;bottom:14px;left:14px;z-index:150;background:#1a1d27;border:1px solid #2d3148;border-radius:12px;padding:10px;width:185px}
#toast{position:fixed;bottom:16px;right:16px;padding:10px 16px;border-radius:8px;font-size:.8rem;font-weight:600;z-index:999;opacity:0;transition:.3s;background:#1a1d27;border-left:4px solid #7c6fff;color:#e2e8f0;max-width:260px;pointer-events:none}
#toast.S{opacity:1}#toast.ok{border-color:#10b981}#toast.er{border-color:#f87171}
.stt{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#64748b;margin-bottom:11px}
.spin{width:32px;height:32px;border:3px solid #2d3148;border-top-color:#7c6fff;border-radius:50%;animation:sp 1s linear infinite;margin:16px auto}
@keyframes sp{to{transform:rotate(360deg)}}
.mt{margin-top:13px}.mb{margin-bottom:13px}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.between{justify-content:space-between}
.dim{color:#64748b}.sm{font-size:.78rem}
.tab-bar{display:flex;background:#1a1d27;border-radius:9px;padding:3px;border:1px solid #2d3148;margin-bottom:16px}
.tab{flex:1;padding:7px;text-align:center;border:none;background:none;color:#64748b;cursor:pointer;border-radius:8px;font:600 .8rem Inter;transition:.15s}
.tab.on{background:#7c6fff;color:#fff}
@media(max-width:600px){.g2,.g4{grid-template-columns:1fr}.tgrid{grid-template-columns:1fr}}
</style></head><body>

<div id="AUTH" style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:radial-gradient(ellipse at 25% 25%,rgba(124,111,255,.12),transparent 55%),#0f1117">
  <div style="width:100%;max-width:350px">
    <div style="text-align:center;margin-bottom:20px"><div style="font-size:2rem;margin-bottom:4px">🎓</div><div style="font-size:1.4rem;font-weight:800;color:#7c6fff">AI Coach</div><div class="dim sm" style="margin-top:3px">NEET &amp; JEE Preparation</div></div>
    <div class="tab-bar"><button class="tab on" id="tL" onclick="swTab(0)">Login</button><button class="tab" id="tS" onclick="swTab(1)">Sign Up</button></div>
    <div id="LP"><div class="f"><label>Username</label><input id="LU" onkeydown="if(event.key=='Enter')doLogin()"/></div><div class="f"><label>Password</label><input id="LP2" type="password" onkeydown="if(event.key=='Enter')doLogin()"/></div><div id="LE" class="H" style="color:#f87171;font-size:.75rem;margin-bottom:9px"></div><button class="btn bp bw" onclick="doLogin()">Login →</button></div>
    <div id="SP" class="H"><div class="f"><label>Username</label><input id="SU" onkeydown="if(event.key=='Enter')doSignup()"/></div><div class="f"><label>Password</label><input id="SP2" type="password" onkeydown="if(event.key=='Enter')doSignup()"/></div><div id="SE" class="H" style="color:#f87171;font-size:.75rem;margin-bottom:9px"></div><button class="btn bp bw" onclick="doSignup()">Create Account →</button></div>
  </div>
</div>

<div id="APP" class="H">
  <nav>
    <div class="logo">🎓 AI Coach</div>
    <div class="nls">
      <button class="nl on" onclick="nav(0)">Home</button>
      <button class="nl" onclick="nav(1)">Quiz</button>
      <button class="nl" onclick="nav(2)">Review</button>
      <button class="nl" onclick="nav(3)">Analytics</button>
      <button class="nl" onclick="nav(4)">Schedule</button>
      <button class="nl" onclick="nav(5)">Upload</button>
      <button class="nl" onclick="nav(6)">Chat</button>
    </div>
    <div class="nr"><div class="av" id="AV">U</div><span id="UN"></span><button class="btn bs" style="padding:4px 10px;font-size:.72rem" onclick="doLogout()">Logout</button></div>
  </nav>

  <!-- HOME -->
  <div id="P0" class="wrap">
    <div class="card mb" style="background:linear-gradient(135deg,#1e1a3e,#1a1d27);border-color:#3d3580">
      <div style="font-size:1.3rem;font-weight:800">Hey <span id="HN">there</span> 👋</div>
      <div class="dim sm mt" id="HE">Choose your exam below</div>
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:14px">
        <div style="background:rgba(255,255,255,.05);border-radius:8px;padding:10px;text-align:center"><div style="font:800 1.2rem monospace" id="S1">0</div><div class="dim" style="font-size:.62rem;margin-top:2px">Sessions</div></div>
        <div style="background:rgba(255,255,255,.05);border-radius:8px;padding:10px;text-align:center"><div style="font:800 1.2rem monospace" id="S2">—%</div><div class="dim" style="font-size:.62rem">Avg Score</div></div>
        <div style="background:rgba(255,255,255,.05);border-radius:8px;padding:10px;text-align:center"><div style="font:800 1.2rem monospace" id="S3">—</div><div class="dim" style="font-size:.62rem">Level</div></div>
        <div style="background:rgba(255,255,255,.05);border-radius:8px;padding:10px;text-align:center"><div style="font:800 1.2rem monospace" id="S4">—</div><div class="dim" style="font-size:.62rem">Peak Hour</div></div>
        <div style="background:rgba(255,255,255,.05);border-radius:8px;padding:10px;text-align:center;cursor:pointer" onclick="nav(2)"><div style="font:800 1.2rem monospace;color:#f59e0b" id="S5">0</div><div class="dim" style="font-size:.62rem">Review Due</div></div>
      </div>
    </div>
    <div id="EP"><div class="stt">Choose Your Exam</div><div class="g2"><div class="tile" onclick="setExam('NEET')"><div style="font-size:2rem;margin-bottom:7px">🧬</div><div style="font-weight:700">NEET</div><div class="dim sm mt">Physics · Chemistry · Biology</div></div><div class="tile" onclick="setExam('JEE')"><div style="font-size:2rem;margin-bottom:7px">⚙️</div><div style="font-weight:700">JEE</div><div class="dim sm mt">Physics · Chemistry · Maths</div></div></div></div>
    <div id="QA" class="H mt">
      <div class="stt">Quick Actions</div>
      <div class="g4">
        <div class="tile" onclick="nav(1)"><div style="font-size:1.4rem;margin-bottom:5px">🧪</div><div style="font-weight:700;font-size:.9rem">AI Quiz</div></div>
        <div class="tile" onclick="goPYQ()"><div style="font-size:1.4rem;margin-bottom:5px">📋</div><div style="font-weight:700;font-size:.9rem">PYQ Mode</div></div>
        <div class="tile" onclick="goExam()"><div style="font-size:1.4rem;margin-bottom:5px">⏱️</div><div style="font-weight:700;font-size:.9rem">Exam Mode</div></div>
        <div class="tile" onclick="nav(2)"><div style="font-size:1.4rem;margin-bottom:5px">🔁</div><div style="font-weight:700;font-size:.9rem">Review</div></div>
      </div>
      <div id="weakBox" class="H mt"><div class="stt" style="color:#f87171">⚠️ Weak Topics — Practice These</div><div id="weakList"></div></div>
    </div>
  </div>

  <!-- QUIZ -->
  <div id="P1" class="H">
    <div class="wrap">
      <div id="Q1">
        <div class="card mb">
          <div class="row between mb">
            <div class="stt" style="margin:0">Previous Year Papers</div>
            <div class="dim sm" id="paperHint">Select your exam to load papers.</div>
          </div>
          <div id="paperGrid" class="g2"><div class="dim sm">Choose your exam on the home page to unlock papers.</div></div>
        </div>
        <div class="row mb between"><div class="stt" style="margin:0">Select Subject</div><div class="row" style="gap:6px"><button class="btn bs" style="font-size:.75rem;padding:5px 11px" onclick="goPYQ()">📋 PYQ</button><button class="btn br" style="font-size:.75rem;padding:5px 11px" onclick="goExam()">⏱️ Exam</button></div></div>
        <div class="g4" id="SG"></div>
      </div>
      <div id="Q2" class="H">
        <div class="row mb"><button class="btn bs" style="padding:5px 12px;font-size:.76rem" onclick="gostep(1)">← Back</button><div class="stt" style="margin:0">Topic — <span id="SL" style="color:#a5b4fc"></span></div></div>
        <div class="chips" id="TC"></div>
        <div id="TC2" class="H mt"><div class="card row between" style="border-color:rgba(124,111,255,.3)"><span style="font-weight:600;font-size:.9rem" id="TN"></span><button class="btn bp" onclick="doQuiz()">Generate Quiz →</button></div></div>
      </div>
      <div id="Q3" class="H">
        <div id="normHUD" class="hud">
          <div><div class="dim" style="font-size:.62rem;text-transform:uppercase;letter-spacing:.06em">Quiz</div><div style="font-weight:600;font-size:.8rem" id="HI">—</div></div>
          <div class="pb"><div class="pbi" id="HP" style="width:0%"></div></div>
          <div id="LVD"></div>
          <div class="row" style="gap:3px"><div id="FD" style="display:flex;gap:2px"><span class="fd"></span><span class="fd"></span><span class="fd"></span><span class="fd"></span><span class="fd"></span></div><div id="FL" style="font-size:.67rem;font-weight:700;color:#10b981">Fresh</div></div>
          <div class="dim sm" id="HQ">Q1/5</div><div class="ht" id="HT">0s</div>
        </div>
        <div id="examHUD" class="H" style="background:linear-gradient(135deg,#1e0808,#1a1d27);border:1px solid rgba(248,113,113,.4);border-radius:11px;padding:11px 15px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
          <div><div style="font-size:.68rem;font-weight:700;color:#f87171;text-transform:uppercase">EXAM MODE</div><div style="font-weight:600;font-size:.8rem" id="EHI">—</div></div>
          <div class="pb"><div class="pbi" id="EHP" style="width:0%;background:#f87171"></div></div>
          <div class="dim sm" id="EHQ">Q1</div>
          <div class="etimer" id="eTimer">3:00:00</div>
          <div style="background:rgba(248,113,113,.1);color:#f87171;padding:3px 9px;border-radius:7px;font-size:.75rem;font-weight:700">-1 Wrong</div>
        </div>
        <div id="QL"><div class="spin"></div><div class="dim sm" style="text-align:center;margin-top:5px">Generating questions…</div></div>
        <div id="QD" class="H">
          <div id="tCard" class="tcard H">
            <div style="font-weight:800;font-size:.95rem;color:#a5b4fc;margin-bottom:9px">📚 Quick Review — <span id="tcT"></span></div>
            <div id="tcA" style="font-size:.84rem;color:#e2e8f0;line-height:1.6;margin-bottom:8px"></div>
            <div class="tgrid"><div class="ti"><div class="ti-l">💡 Remember</div><div class="ti-v" id="tcTip"></div></div><div class="ti"><div class="ti-l">🖼️ Visualise</div><div class="ti-v" id="tcDia"></div></div></div>
            <div class="formula" id="tcF"></div>
            <div class="mt row" style="justify-content:flex-end"><button class="btn bg" style="font-size:.78rem;padding:6px 14px" onclick="hideTeach()">Got it, show question →</button></div>
          </div>
          <div class="qcard" id="QArea">
            <div class="qn" id="QN">Q1 OF 5</div>
            <div id="QVH" class="H" style="background:rgba(124,111,255,.07);border:1px solid rgba(124,111,255,.18);border-radius:8px;padding:10px 13px;margin-bottom:13px;font-size:.8rem;color:#a5b4fc"></div>
            <div class="qt" id="QT"></div>
            <div class="opts" id="QO"></div>
            <div class="exp H" id="QE"></div>
            <div id="explRow" class="H mt"><button class="btn bs" style="font-size:.74rem;padding:5px 11px" onclick="explainWrong()">🤖 Explain step-by-step</button></div>
          </div>
          <div id="QNX" class="H" style="text-align:right;margin-top:12px">
            <span class="sm" id="QA2" style="margin-right:10px"></span>
            <button class="btn bp" onclick="nextQ()">Next →</button>
          </div>
        </div>
      </div>
      <div id="Q4" class="H">
        <div class="card" style="text-align:center;padding:30px">
          <div style="font-size:1.2rem;font-weight:800;margin-bottom:16px">Quiz Complete! 🎉</div>
          <div class="rpct" id="RP">0%</div>
          <div id="RB" style="margin:9px 0"></div>
          <div class="dim sm" id="RM" style="margin-bottom:14px"></div>
          <div id="lvlMsg" class="H" style="background:rgba(124,111,255,.08);border:1px solid rgba(124,111,255,.25);border-radius:9px;padding:11px;margin-bottom:13px;font-size:.84rem"></div>
          <div class="row" style="justify-content:center;gap:9px;flex-wrap:wrap">
            <button class="btn bs" onclick="gostep(1)">New Topic</button>
            <button class="btn bg" onclick="nav(2)">Review Mistakes</button>
            <button class="btn bp" onclick="nav(3)">Analytics →</button>
          </div>
        </div>
      </div>
      <div id="Q5" class="H">
        <div class="card mb" style="background:linear-gradient(135deg,#1e0808,#1a1d27);border-color:rgba(248,113,113,.35)">
          <div class="row between mb">
            <button class="btn bs" style="padding:5px 12px;font-size:.76rem" onclick="exitPaperExam()">← Back</button>
            <div class="row" style="gap:7px">
              <button class="btn bs" style="font-size:.74rem;padding:5px 11px" onclick="openPaperFile()">Open PDF</button>
              <button class="btn br" style="font-size:.74rem;padding:5px 11px" onclick="finishPaperExam()">Finish Exam</button>
            </div>
          </div>
          <div class="row between" style="align-items:flex-start">
            <div>
              <div class="stt" style="color:#f87171;margin-bottom:5px">Previous Year Exam Mode</div>
              <div style="font-size:1rem;font-weight:700" id="paperTitle">—</div>
              <div class="dim sm mt" id="paperMeta">—</div>
            </div>
            <div class="etimer" id="paperTimer">3:00:00</div>
          </div>
        </div>
        <div class="card" style="padding:0;overflow:hidden">
          <iframe id="paperFrame" title="Previous year paper" style="width:100%;height:72vh;border:none;background:#fff"></iframe>
        </div>
      </div>
    </div>
  </div>

  <!-- REVIEW -->
  <div id="P2" class="H wrap">
    <div class="row between mb"><div class="stt" style="margin:0">🔁 Spaced Repetition Review</div><button class="btn bp" style="font-size:.76rem;padding:5px 12px" onclick="loadReview()">Refresh</button></div>
    <div id="srLoad"><div class="spin"></div></div>
    <div id="srContent" class="H"><div id="srDue" class="mb"></div><div class="stt">Strong Topics ✅</div><div id="srStrong"></div></div>
  </div>

  <!-- ANALYTICS -->
  <div id="P3" class="H wrap">
    <div class="stt">Performance Analytics</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:16px">
      <div class="card" style="text-align:center;padding:13px"><div style="font:800 1.3rem monospace" id="A1">0</div><div class="dim" style="font-size:.63rem;margin-top:2px">Sessions</div></div>
      <div class="card" style="text-align:center;padding:13px"><div style="font:800 1.3rem monospace" id="A2">—%</div><div class="dim" style="font-size:.63rem">Avg Score</div></div>
      <div class="card" style="text-align:center;padding:13px"><div style="font:800 1.3rem monospace" id="A3">—</div><div class="dim" style="font-size:.63rem">Best Subject</div></div>
      <div class="card" style="text-align:center;padding:13px"><div style="font:800 1.3rem monospace" id="A4">—</div><div class="dim" style="font-size:.63rem">Peak Hour</div></div>
      <div class="card" style="text-align:center;padding:13px"><div style="font:800 1.3rem monospace;color:#f59e0b" id="A5">—</div><div class="dim" style="font-size:.63rem">Avg Fatigue</div></div>
    </div>
    <div class="g2 mb"><div class="card"><div class="stt" style="color:#f87171">⚠️ Weak Topics</div><div id="aWeak"><div class="dim sm">Need 3+ sessions per topic</div></div></div><div class="card"><div class="stt" style="color:#10b981">✅ Strong Topics</div><div id="aStrong"><div class="dim sm">Need 3+ sessions per topic</div></div></div></div>
    <div class="g2 mb">
      <div class="card"><div class="stt">Hourly Performance</div><div id="HC" style="display:flex;align-items:flex-end;gap:3px;height:80px"></div><div style="height:14px"></div><div style="display:flex;gap:10px;font-size:.67rem;color:#64748b"><span>🟣 Peak</span><span>🟡 Mid</span><span>🔴 Low</span></div></div>
      <div class="card"><div class="stt">Subject Scores</div><div id="SC"></div></div>
    </div>
    <div class="card mb"><div class="stt">📅 Study Plan</div><div id="PG" class="g3"></div></div>
    <div class="stt">Recent Sessions</div><div id="RL"><div class="dim sm">No sessions yet.</div></div>
  </div>

  <!-- UPLOAD -->
  <div id="P5" class="H wrap">
    <div class="stt">📄 Upload Notes → Generate MCQs</div>
    <div class="card mb">
      <div class="f"><label>Subject</label><select id="upSubj"></select></div>
      <div class="f"><label>Paste your notes here</label>
        <textarea id="upText" rows="7" style="resize:vertical" placeholder="Paste chapter content, formulae, notes…"></textarea>
      </div>
      <div id="upDrop" style="border:2px dashed #2d3148;border-radius:9px;padding:20px;text-align:center;cursor:pointer;margin-top:8px" onclick="document.getElementById('upFile').click()">
        <div style="font-size:1.7rem;margin-bottom:4px">📁</div>
        <div style="font-weight:600;font-size:.83rem">Or drop a .txt file here</div>
        <input type="file" id="upFile" accept=".txt" class="H" onchange="readUpFile(event)"/>
      </div>
    </div>
    <button class="btn bp bw" id="upBtn" onclick="genUploadMCQ()">Generate MCQs →</button>
    <div id="upLoad" class="H" style="text-align:center;padding:22px"><div class="spin"></div><div class="dim sm" style="margin-top:5px">AI reading your notes…</div></div>
    <div id="upRes" class="H" style="margin-top:16px"></div>
  </div>

  <!-- SCHEDULE -->
  <div id="P4" class="H wrap">
    <div class="row between mb"><div class="stt" style="margin:0">📅 AI Study Schedule</div><button class="btn bp" style="font-size:.76rem;padding:5px 12px" id="schBtn" onclick="genSched()">Generate My Schedule →</button></div>
    <div class="card mb" style="font-size:.82rem;color:#94a3b8;line-height:1.6">AI generates a personalised 7-day schedule based on your peak hours, weak topics, and exam syllabus.</div>
    <div id="schLoad" class="H"><div class="spin"></div><div class="dim sm" style="text-align:center;margin-top:5px">Planning your week…</div></div>
    <div id="schContent" class="H"></div>
  </div>

  <!-- CHAT -->
  <div id="P6" class="H" style="height:calc(100vh - 54px);display:none;flex-direction:column">
    <div style="max-width:700px;margin:0 auto;width:100%;padding:0 18px;flex:1;display:flex;flex-direction:column">
      <div id="CM" style="flex:1;overflow-y:auto;padding:16px 0;display:flex;flex-direction:column;gap:10px">
        <div style="align-self:flex-start;background:#1a1d27;border:1px solid #2d3148;border-radius:13px;border-bottom-left-radius:3px;padding:10px 14px;font-size:.84rem;max-width:85%;line-height:1.6">
          👋 Hi! I'm your AI Study Coach. Ask me anything — concepts, wrong answer explanations, study strategies!
        </div>
      </div>
      <div style="padding:8px 0 16px;display:flex;gap:7px">
        <input id="CI" style="flex:1;padding:10px 13px;background:#1a1d27;border:1.5px solid #2d3148;border-radius:9px;color:#e2e8f0;font:inherit;font-size:.84rem" placeholder="Ask anything…" onkeydown="if(event.key==='Enter')sendChat()"/>
        <button class="btn bp" onclick="sendChat()">Send</button>
      </div>
    </div>
  </div>
</div>

<!-- SELF REPORT -->
<div class="modal H" id="SRM">
  <div class="mbox">
    <div style="font-size:1.1rem;font-weight:800;margin-bottom:5px">How are you feeling? 🧠</div>
    <div class="dim sm mb">Improves fatigue detection accuracy</div>
    <div class="g3" style="gap:10px;margin:14px 0">
      <div class="tile" style="padding:14px 8px" onclick="submitSR('sharp')"><div style="font-size:1.7rem;margin-bottom:4px">😊</div><div style="font-weight:700;font-size:.8rem">Sharp</div></div>
      <div class="tile" style="padding:14px 8px" onclick="submitSR('okay')"><div style="font-size:1.7rem;margin-bottom:4px">😐</div><div style="font-weight:700;font-size:.8rem">Okay</div></div>
      <div class="tile" style="padding:14px 8px" onclick="submitSR('tired')"><div style="font-size:1.7rem;margin-bottom:4px">😴</div><div style="font-weight:700;font-size:.8rem">Tired</div></div>
    </div>
    <button class="btn bs bw" onclick="H('SRM')">Skip</button>
  </div>
</div>

<div id="FOV" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.86);z-index:200;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)">
  <div class="ovb">
    <div style="font-size:2.5rem;margin-bottom:8px">😴</div>
    <div style="font-size:1.1rem;font-weight:800;margin-bottom:5px">Fatigue Detected!</div>
    <div id="fatSigs" style="background:#1e2235;border-radius:9px;padding:11px;margin-bottom:13px;text-align:left;font-size:.77rem"></div>
    <div class="g3" id="RG"></div>
    <button class="btn bs mt bw" style="font-size:.77rem" onclick="skipRec()">Skip</button>
  </div>
</div>
<div id="VOV" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.97);z-index:400;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:20px">
  <div style="color:#e2e8f0;font-weight:700;font-size:.94rem" id="VT">Recovery</div>
  <div class="vtm" id="VC">5:00</div>
  <div class="dim sm">Relax completely</div>
  <iframe class="vfr" id="VF" src="" allow="autoplay" allowfullscreen></iframe>
  <div class="row"><button class="btn bs" onclick="extRec()">+10 min</button><button class="btn bg" onclick="endRec()">I'm Ready →</button></div>
</div>

<div class="lvt" id="lvlT"></div>

<div id="WCV" class="H">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <div style="font-size:.67rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Eye Tracking</div>
    <div id="WCS" style="width:7px;height:7px;border-radius:50%;background:#f87171"></div>
  </div>
  <canvas id="WCC" width="175" height="105" style="width:100%;border-radius:7px;background:#1e2235"></canvas>
  <div style="margin-top:6px;display:flex;flex-direction:column;gap:3px">
    <div style="display:flex;justify-content:space-between;font-size:.68rem"><span class="dim">Blink/min</span><span id="WBR" style="font-weight:700;font-family:monospace">—</span></div>
    <div style="display:flex;justify-content:space-between;font-size:.68rem"><span class="dim">Eye open</span><span id="WEO" style="font-weight:700;font-family:monospace">—</span></div>
    <div style="display:flex;justify-content:space-between;font-size:.68rem"><span class="dim">Focus</span><span id="WFS" style="font-weight:700;font-family:monospace">—</span></div>
  </div>
  <div id="WCE" class="H" style="font-size:.67rem;color:#f59e0b;margin-top:5px"></div>
  <button onclick="showFAT(FAT.sigs)" style="width:100%;margin-top:7px;padding:5px;background:#1e2235;border:1px solid #2d3148;border-radius:7px;color:#64748b;font-size:.67rem;cursor:pointer;font-family:inherit">🧪 Test Recovery</button>
</div>
<div id="toast"></div>

<script>
const ST={uid:null,uname:null,exam:null,subj:null,top:null,lv:'mid',sid:null,qs:[],qi:0,sc:0,times:[],streak:0,cs:0,ws:0,qstart:0,tint:null,rsecs:300,rint:null,running:false,mode:'normal',esecs:0,eint:null,lastWQ:'',lastWA:'',sf:'',selfAt:10,totalAns:0,paperId:null,paperTitle:'',paperMinutes:0};
const FAT={kt:[],lk:0,sp:[],ac:0,li:-1,wa:false,s:null,br:0,eo:1.0,bw:[],cf:0,score:0,sigs:{}};
window._papers=[];

const $=id=>document.getElementById(id);
const H=id=>$(id)&&$(id).classList.add('H');
const SH=id=>$(id)&&$(id).classList.remove('H');

function toast(m,t){const el=$('toast');el.textContent=m;el.className='S'+(t?' '+t:'');clearTimeout(el._t);el._t=setTimeout(()=>el.className='',3200);}

function swTab(i){$('tL').className='tab'+(i===0?' on':'');$('tS').className='tab'+(i===1?' on':'');i===0?(SH('LP'),H('SP')):(H('LP'),SH('SP'));}

async function doLogin(){
  const u=$('LU').value.trim(),p=$('LP2').value;if(!u||!p)return;
  try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const d=await r.json();if(!r.ok){showErr('LE',d.detail||'Failed');return;}boot(d);}
  catch(e){showErr('LE','Server error - is app.py running?');}
}
async function doSignup(){
  const u=$('SU').value.trim(),p=$('SP2').value;if(!u||!p){showErr('SE','Fill all fields');return;}
  try{const r=await fetch('/api/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const d=await r.json();if(!r.ok){showErr('SE',d.detail||'Failed');return;}boot(d);}
  catch(e){showErr('SE','Server error');}
}
function showErr(id,m){const e=$(id);e.textContent=m;SH(id);setTimeout(()=>H(id),4000);}
function boot(d){ST.uid=d.id;ST.uname=d.username;ST.exam=d.exam||null;$('AV').textContent=d.username[0].toUpperCase();$('UN').textContent=d.username;$('HN').textContent=d.username;H('AUTH');SH('APP');if(ST.exam){$('HE').textContent='Preparing for '+ST.exam;H('EP');SH('QA');}loadStats();loadPaperCards();}
function doLogout(){clearInterval(ST.eint);clearInterval(ST.tint);ST.uid=null;ST.running=false;ST.qs=[];ST.paperId=null;window._papers=[];if($('paperFrame'))$('paperFrame').src='';stopCam();H('APP');SH('AUTH');}

function nav(i){
  if(i===1&&ST.running)return;
  [0,1,2,3,4,5,6].forEach(n=>{const p=$('P'+n);if(!p)return;if(n===i){p.classList.remove('H');if(n===6)p.style.display='flex';}else{p.classList.add('H');if(n===6)p.style.display='none';}});
  document.querySelectorAll('.nl').forEach((b,n)=>n===i?b.classList.add('on'):b.classList.remove('on'));
  if(i===0)loadStats();if(i===2)loadReview();if(i===3)loadAnalytics();if(i===5)loadUpSubjs();if(i===1&&!ST.running){loadSubjs();loadPaperCards();}
}

async function setExam(e){ST.exam=e;$('HE').textContent='Preparing for '+e;H('EP');SH('QA');try{await fetch('/api/exam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:ST.uid,exam_type:e})});}catch(e){}toast(e+' activated 🎯','ok');loadStats();loadPaperCards();}

async function loadStats(){
  try{
    const d=await(await fetch('/api/stats/'+ST.uid)).json();
    const rs=d.recent||[],sp=d.subjects||[],hp=d.hourly||[];
    $('S1').textContent=rs.length;
    if(sp.length)$('S2').textContent=Math.round(sp.reduce((a,x)=>a+x.avg,0)/sp.length)+'%';
    const lvs=rs.map(x=>x.lv),b=lvs.filter(l=>l==='bright').length,m=lvs.filter(l=>l==='mid').length;
    if(rs.length)$('S3').textContent=b>m?'Bright':m>0?'Mid':'Low';
    const ph=hp.filter(h=>h.type==='peak');if(ph.length)$('S4').textContent=ph[0].h+':00';
    $('S5').textContent=d.spaced_rep_due||0;
    window._ph=hp.filter(h=>h.type==='peak').map(h=>h.h);window._mh=hp.filter(h=>h.type==='mid').map(h=>h.h);
    if(d.weak_topics&&d.weak_topics.length){SH('weakBox');$('weakList').innerHTML=d.weak_topics.slice(0,5).map(t=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 11px;background:#1e2235;border-radius:8px;margin-bottom:6px"><div><span style="font-weight:600;font-size:.82rem">${t.topic}</span><span class="dim sm"> · ${t.subject}</span></div><div class="row" style="gap:7px"><span style="font:700 .8rem monospace;color:#f87171">${t.acc}%</span><button class="btn bp" style="font-size:.7rem;padding:4px 9px" onclick="qPractice('${t.subject}','${t.topic}')">Practice</button></div></div>`).join('');}
  }catch(e){}
}
function qPractice(s,t){ST.subj=s;ST.top=t;nav(1);setTimeout(doQuiz,150);}

const SUBJS={NEET:{Physics:['Motion & Laws','Work Energy Power','Gravitation','Thermodynamics','Waves','Ray Optics','Electrostatics','Current Electricity','Magnetic Effects','Atoms & Nuclei','Semiconductors'],Chemistry:['Atomic Structure','Chemical Bonding','Thermochemistry','Equilibrium','Electrochemistry','Organic Basics','Hydrocarbons','Biomolecules','Polymers','Coordination Compounds','p-Block'],Biology:['Cell Biology','Cell Division','Genetics','Molecular Inheritance','Evolution','Digestion','Respiration','Excretion','Nervous System','Reproduction','Plant Physiology','Ecology','Biotechnology']},JEE:{Physics:['Kinematics','Laws of Motion','Work Energy','Rotation','Gravitation','SHM','Waves','Electrostatics','Current Electricity','EMI','Optics','Modern Physics'],Chemistry:['Mole Concept','Chemical Bonding','States of Matter','Thermodynamics','Kinetics','Electrochemistry','Organic Reactions','Stereochemistry','Coordination','d-f Block','p-Block'],Mathematics:['Sets','Trigonometry','Complex Numbers','Quadratics','Sequences','Binomial','Coordinate Geometry','3D Geometry','Vectors','Limits','Derivatives','Integration','Differential Equations','Probability','Matrices']}};
const ICONS={Physics:'⚡',Chemistry:'🧪',Biology:'🧬',Mathematics:'📐'};

async function loadPaperCards(){
  const grid=$('paperGrid'),hint=$('paperHint');
  if(!grid||!hint)return;
  if(!ST.exam){
    hint.textContent='Select your exam to load papers.';
    grid.innerHTML='<div class="dim sm">Choose your exam on the home page to unlock papers.</div>';
    window._papers=[];
    return;
  }
  hint.textContent='Loading papers…';
  grid.innerHTML='<div class="dim sm">Loading papers…</div>';
  try{
    const r=await fetch('/api/papers?exam='+encodeURIComponent(ST.exam));
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Could not load papers');
    window._papers=d.papers||[];
    hint.textContent=window._papers.length?`${window._papers.length} papers ready`:'No papers added yet';
    grid.innerHTML=window._papers.length?window._papers.map(p=>`<div class="card" style="padding:15px;border-color:rgba(248,113,113,.22)"><div class="row between" style="align-items:flex-start;margin-bottom:8px"><div><div style="font-weight:700;font-size:.92rem">${p.title}</div><div class="dim sm" style="margin-top:4px">${p.source} · ${p.year}</div></div><span style="font-size:.68rem;background:rgba(248,113,113,.12);color:#f87171;padding:3px 8px;border-radius:999px;font-weight:700">${p.exam}</span></div><div class="dim sm" style="line-height:1.6;margin-bottom:12px">${p.questions} questions · ${Math.floor(p.duration_minutes/60)}h ${p.duration_minutes%60}m</div><button class="btn br bw" onclick="startPaperExam('${p.id}')">Start Exam Mode →</button></div>`).join(''):'<div class="dim sm">No papers found for this exam yet.</div>';
  }catch(e){
    window._papers=[];
    hint.textContent='Paper library unavailable';
    grid.innerHTML='<div class="dim sm">Could not load previous year papers right now.</div>';
  }
}
function loadSubjs(){gostep(1);$('SG').innerHTML='';Object.keys(SUBJS[ST.exam||'NEET']||SUBJS.NEET).forEach(s=>{const d=document.createElement('div');d.className='tile';d.innerHTML=`<div style="font-size:1.7rem;margin-bottom:5px">${ICONS[s]||'📖'}</div><div style="font-weight:700;font-size:.88rem">${s}</div>`;d.onclick=()=>pickSubj(s);$('SG').appendChild(d);});}
function pickSubj(s){ST.subj=s;$('SL').textContent=s;$('TC').innerHTML='';(SUBJS[ST.exam||'NEET']?.[s]||[]).forEach(t=>{const c=document.createElement('div');c.className='chip';c.textContent=t;c.onclick=()=>pickTop(t,c);$('TC').appendChild(c);});H('TC2');gostep(2);}
function pickTop(t,el){ST.top=t;document.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');$('TN').textContent=ST.subj+' → '+t;SH('TC2');}
function gostep(n){[1,2,3,4,5].forEach(i=>i===n?$('Q'+i).classList.remove('H'):$('Q'+i).classList.add('H'));if(n<=2){ST.qs=[];ST.qi=0;ST.sc=0;ST.running=false;ST.mode='normal';ST.paperId=null;clearInterval(ST.eint);if($('paperFrame'))$('paperFrame').src='';H('examHUD');SH('normHUD');}}
function goPYQ(){if(!ST.subj){nav(1);toast('Select a subject to start PYQ mode','ok');return;}ST.mode='pyq';doQuiz();}
function goExam(){if(!ST.subj||!ST.top){nav(1);toast('Select a subject and topic to start Exam mode','ok');return;}ST.mode='exam';doQuiz();}

async function doQuiz(){
  if(!ST.subj)return;if(ST.mode!=='pyq'&&!ST.top){toast('Pick a topic first','er');return;}
  ST.qi=0;ST.sc=0;ST.times=[];ST.streak=0;ST.cs=0;ST.ws=0;ST.totalAns=0;ST.selfAt=10;ST.running=false;
  gostep(3);if(ST.mode==='exam'){SH('examHUD');H('normHUD');}else{SH('normHUD');H('examHUD');}
  SH('QL');H('QD');H('QNX');$('HI').textContent=(ST.subj||'')+(ST.top?' — '+ST.top:'');$('EHI').textContent=ST.subj||'';
  try{
    const r=await fetch('/api/quiz',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:ST.uid,subject:ST.subj,topic:ST.top||ST.subj,level:ST.lv,exam:ST.exam||'NEET',mode:ST.mode})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Error '+r.status);
    if(!Array.isArray(d.questions)||!d.questions.length)throw new Error('No questions returned');
    ST.sid=d.sid;ST.qs=d.questions;ST.lv=d.level||ST.lv;ST.running=true;
    H('QL');SH('QD');updLvBadge();initFAT();if(ST.mode==='exam')startETimer();showQ();
  }catch(e){toast('Quiz failed: '+e.message,'er');console.error(e);gostep(2);}
}

function renderTimer(id,secs){
  const el=$(id);if(!el)return;
  const safe=Math.max(0,secs),h=Math.floor(safe/3600),m=Math.floor((safe%3600)/60),s=safe%60;
  el.textContent=h+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
  el.style.color=safe<600?'#f87171':'#e2e8f0';
}
function startETimer(total=10800,target='eTimer',onDone=endQuiz){
  ST.esecs=total;clearInterval(ST.eint);renderTimer(target,ST.esecs);
  ST.eint=setInterval(()=>{ST.esecs--;renderTimer(target,ST.esecs);if(ST.esecs<=0){clearInterval(ST.eint);toast('Time up!','er');onDone(true);}},1000);
}
function openPaperFile(){if(ST.paperId)window.open('/api/papers/'+encodeURIComponent(ST.paperId),'_blank');}
function startPaperExam(paperId){
  const paper=(window._papers||[]).find(p=>p.id===paperId);
  if(!paper){toast('Paper not found','er');return;}
  clearInterval(ST.tint);stopCam();
  ST.qs=[];ST.qi=0;ST.sc=0;ST.running=true;ST.mode='paper';ST.paperId=paper.id;ST.paperTitle=paper.title;ST.paperMinutes=paper.duration_minutes;
  $('paperTitle').textContent=paper.title;
  $('paperMeta').textContent=`${paper.exam} · ${paper.questions} questions · ${Math.floor(paper.duration_minutes/60)}h ${paper.duration_minutes%60}m`;
  $('paperFrame').src='/api/papers/'+encodeURIComponent(paper.id);
  gostep(5);
  startETimer(paper.duration_minutes*60,'paperTimer',finishPaperExam);
}
function finishPaperExam(auto=false){
  const title=ST.paperTitle||'paper exam';
  clearInterval(ST.eint);
  ST.running=false;ST.mode='normal';ST.paperId=null;ST.paperTitle='';ST.paperMinutes=0;
  if($('paperFrame'))$('paperFrame').src='';
  gostep(1);
  toast(auto?`Time up for ${title}`:`Closed ${title}`,auto?'er':'ok');
}
function exitPaperExam(){finishPaperExam(false);}

async function showQ(){
  const q=ST.qs[ST.qi];if(!q){endQuiz();return;}
  const tot=ST.qs.length;
  $('HP').style.width=(ST.qi/tot*100)+'%';$('EHP').style.width=(ST.qi/tot*100)+'%';
  $('HQ').textContent='Q'+(ST.qi+1)+'/'+tot+' · '+ST.sc+' ✓';$('EHQ').textContent='Q'+(ST.qi+1)+'/'+tot;
  $('QN').textContent='QUESTION '+(ST.qi+1)+' OF '+tot;$('QT').textContent=q.question;
  if(q.visual_aid&&q.visual_aid.trim()&&ST.lv==='mid'){$('QVH').textContent='💡 '+q.visual_aid;SH('QVH');}else H('QVH');
  H('tCard');
  if(ST.lv==='low'&&ST.mode==='normal'&&(q.formula||q.visual_aid||q.diagram)){await showTeach(q);return;}
  renderQ(q);
}

async function showTeach(q){
  $('tcT').textContent=ST.top||ST.subj;$('tcA').textContent=q.visual_aid||'Visualise this in daily life.';$('tcTip').textContent=q.diagram||'Draw a simple diagram.';$('tcDia').textContent=q.diagram||'Think of a real-world example.';$('tcF').textContent=q.formula||'Review the key formula.';SH('tCard');H('QArea');
  if(!q.formula){try{const r=await fetch(`/api/teach?subject=${encodeURIComponent(ST.subj)}&topic=${encodeURIComponent(ST.top||ST.subj)}&exam=${ST.exam||'NEET'}`);if(r.ok){const c=await r.json();$('tcA').textContent=c.analogy||q.visual_aid||'';$('tcTip').textContent=c.remember_tip||'';$('tcDia').textContent=c.diagram||'';$('tcF').textContent=c.key_formula||'';}}catch(e){}}
}
function hideTeach(){H('tCard');SH('QArea');renderQ(ST.qs[ST.qi]);}

function renderQ(q){
  SH('QArea');$('QO').innerHTML='';H('QE');H('QNX');H('explRow');$('QA2').textContent='';
  ['A','B','C','D'].forEach((k,i)=>{if(!q.options[i])return;const b=document.createElement('button');b.className='opt';b.innerHTML='<div class="ok">'+k+'</div><span>'+q.options[i].replace(/^[A-D]\\)\\s*/,'').replace(/</g,'&lt;')+'</span>';b.addEventListener('click',()=>pickAns(i));$('QO').appendChild(b);});
  ST.qstart=Date.now();clearInterval(ST.tint);ST.tint=setInterval(()=>{const s=Math.floor((Date.now()-ST.qstart)/1000);$('HT').textContent=s+'s';$('HT').style.color=s>25?'#f87171':'#e2e8f0';},500);
}

async function pickAns(idx){
  const btns=$('QO').querySelectorAll('.opt');if(!btns.length||btns[0].disabled)return;
  trackAC(idx);clearInterval(ST.tint);const ms=Date.now()-ST.qstart;ST.times.push(ms);
  const q=ST.qs[ST.qi];const chosen=q.options[idx];const right=chosen.trim()===q.correct.trim();
  btns.forEach((b,i)=>{b.disabled=true;if(q.options[i]&&q.options[i].trim()===q.correct.trim())b.classList.add('R');});
  if(!right){btns[idx].classList.add('W');ST.streak++;ST.ws++;ST.cs=0;ST.lastWQ=q.question;ST.lastWA=q.correct;}
  else{ST.sc++;ST.streak=0;ST.cs++;ST.ws=0;}
  ST.totalAns++;
  if(ST.mode==='exam'&&!right)ST.sc=Math.max(0,ST.sc-1);
  if(ST.mode!=='exam'){$('QE').textContent='💡 '+q.explanation;SH('QE');if(!right)SH('explRow');}
  $('QA2').textContent=right?'✅ Correct!':'❌ Wrong';SH('QNX');
  updFAT();
  fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:ST.sid,user_id:ST.uid,subject:ST.subj,topic:ST.top||'',question:q.question,correct:q.correct,answer:chosen,ms:ms,idx:ST.qi,is_correct:right})}).catch(()=>{});
  if(ST.totalAns>=2&&ST.totalAns%2===0&&ST.mode==='normal')checkAdapt();
  if(ST.times.length>=3&&ST.times.length%3===0)chkFAT();
  if(ST.totalAns===ST.selfAt)SH('SRM');
}
function nextQ(){ST.qi++;showQ();}

async function checkAdapt(){try{const r=await fetch(`/api/adapt/${ST.sid}/${ST.cs}/${ST.ws}`);const d=await r.json();if(d.changed){const ol=ST.lv;ST.lv=d.level;updLvBadge();showLvT(ol,d.level,d.reason);}}catch(e){}}
function updLvBadge(){const c={bright:'lb-b',mid:'lb-m',low:'lb-l'}[ST.lv]||'lb-m';const i={bright:'⭐',mid:'📈',low:'🌱'}[ST.lv]||'📈';$('LVD').innerHTML=`<span class="lbadge ${c}">${i} ${ST.lv.toUpperCase()}</span>`;}
function showLvT(ol,nl,r){const t=$('lvlT'),up=nl==='bright'||(nl==='mid'&&ol==='low');t.style.borderColor=up?'#10b981':'#f59e0b';t.style.color=up?'#10b981':'#f59e0b';t.textContent=(up?'⬆️ Level Up! ':'⬇️ Adjusted — ')+r;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),3000);}

async function submitSR(f){H('SRM');ST.sf=f;ST.selfAt+=10;try{await fetch(`/api/self-report?user_id=${ST.uid}&session_id=${ST.sid}&feeling=${f}`,{method:'POST'});}catch(e){}if(f==='tired')chkFAT();else toast(f==='sharp'?'😊 Great focus!':'😐 Stretch soon','ok');}

function explainWrong(){nav(6);setTimeout(()=>{$('CI').value='Explain why I got this wrong with step-by-step solution.';sendChat(true);},200);}

async function endQuiz(){
  clearInterval(ST.tint);clearInterval(ST.eint);ST.running=false;stopCam();
  const pct=Math.round(ST.sc/Math.max(1,ST.qs.length)*100);$('RP').textContent=pct+'%';$('RP').style.color=pct>=75?'#10b981':pct>=50?'#f59e0b':'#f87171';
  const lv=pct>=75?'bright':pct>=50?'mid':'low';const prev=ST.lv;ST.lv=lv;
  const c={bright:'lb-b',mid:'lb-m',low:'lb-l'}[lv];$('RB').innerHTML=`<span class="lbadge ${c}">${{bright:'⭐',mid:'📈',low:'🌱'}[lv]} ${lv.toUpperCase()}</span>`;
  $('RM').textContent={bright:'Excellent! Harder questions next.',mid:'Good work! Keep building.',low:'Visual teaching will help — keep practising.'}[lv];
  if(lv!==prev){SH('lvlMsg');$('lvlMsg').textContent=(lv==='bright'||(lv==='mid'&&prev==='low'))?'🎉 Level upgraded to '+lv+'!':'📚 Adjusted to '+lv+' level.';}
  gostep(4);try{await fetch('/api/level/'+ST.sid);}catch(e){}
}

async function loadReview(){
  H('srContent');SH('srLoad');
  try{
    const d=await(await fetch('/api/spaced-rep/'+ST.uid)).json();
    const wt=await(await fetch('/api/weak-topics/'+ST.uid)).json();
    H('srLoad');SH('srContent');
    const bc=['#f87171','#f59e0b','#a5b4fc','#34d399','#10b981'];
    const bl=['Due Today','Recent Mistakes','3 Days','1 Week','Mastered'];
    $('srDue').innerHTML=d.due.length?`<div class="stt" style="color:#f59e0b">Due for Review (${d.count})</div>`+d.due.map(t=>`<div class="sr-c" style="border-color:${bc[Math.min(4,t.box-1)]}"><div class="row between"><div><div style="font-weight:600;font-size:.87rem">${t.topic}</div><div class="dim sm">${t.subject} · ${bl[Math.min(4,t.box-1)]}</div></div><div class="row" style="gap:6px"><span style="font-size:.72rem;color:#f87171">${t.wrong}× wrong</span><button class="btn bp" style="font-size:.7rem;padding:4px 9px" onclick="qPractice('${t.subject}','${t.topic}')">Review</button></div></div></div>`).join(''):'<div class="card" style="text-align:center;padding:18px"><div style="font-size:1.6rem;margin-bottom:7px">🎉</div><div style="font-weight:700">All caught up!</div></div>';
    $('srStrong').innerHTML=(wt.strong||[]).length?wt.strong.map(t=>`<div class="sr-c" style="border-color:#10b981"><div class="row between"><div style="font-weight:600;font-size:.84rem">${t.topic} <span class="dim">· ${t.subject}</span></div><span style="font:700 .8rem monospace;color:#10b981">${t.acc}%</span></div></div>`).join(''):'<div class="dim sm">Complete more quizzes to identify strong topics.</div>';
  }catch(e){H('srLoad');toast('Failed','er');}
}

async function loadAnalytics(){
  try{
    const d=await(await fetch('/api/stats/'+ST.uid)).json();
    const rs=d.recent||[],sp=d.subjects||[],hp=d.hourly||[];
    $('A1').textContent=rs.length;$('A2').textContent=(sp.length?Math.round(sp.reduce((a,x)=>a+x.avg,0)/sp.length):0)+'%';
    const best=sp.slice().sort((a,b)=>b.avg-a.avg)[0];$('A3').textContent=best?best.s:'—';
    const ph=hp.filter(h=>h.type==='peak');$('A4').textContent=ph.length?ph[0].h+':00':'—';
    $('A5').textContent=d.avg_fatigue||'—';
    $('aWeak').innerHTML=d.weak_topics&&d.weak_topics.length?d.weak_topics.map(t=>`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2d3148;font-size:.8rem"><span>${t.topic} <span class="dim">· ${t.subject}</span></span><span style="color:#f87171;font-weight:700">${t.acc}%</span></div>`).join(''):'<div class="dim sm">Need 3+ sessions per topic</div>';
    $('aStrong').innerHTML=d.strong_topics&&d.strong_topics.length?d.strong_topics.map(t=>`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2d3148;font-size:.8rem"><span>${t.topic} <span class="dim">· ${t.subject}</span></span><span style="color:#10b981;font-weight:700">${t.acc}%</span></div>`).join(''):'<div class="dim sm">Need 3+ sessions per topic</div>';
    const mx=Math.max(...hp.map(h=>h.avg),1);const hc={peak:'#7c6fff',mid:'#f59e0b',low:'#f87171',unknown:'#2d3148'};
    $('HC').innerHTML=hp.length?hp.map(h=>`<div title="${h.h}:00 — ${h.avg}%" style="flex:1;background:${hc[h.type]};height:${Math.max(4,h.avg/mx*74)}px;border-radius:3px 3px 0 0"></div>`).join(''):'<div class="dim sm">No data</div>';
    $('SC').innerHTML=sp.length?sp.map(s=>`<div style="margin-bottom:9px"><div style="display:flex;justify-content:space-between;font-size:.76rem;margin-bottom:3px"><span>${s.s}</span><span style="font-weight:700">${s.avg}%</span></div><div style="background:#1e2235;border-radius:3px;height:4px"><div style="background:${s.avg>=70?'#10b981':s.avg>=50?'#f59e0b':'#f87171'};width:${s.avg}%;height:100%;border-radius:3px"></div></div></div>`).join(''):'<div class="dim sm">No data</div>';
    const plan=d.plan||{};const pc={peak:'#7c6fff',mid:'#f59e0b',low:'#f87171'};
    $('PG').innerHTML=['peak','mid','low'].map(t=>`<div style="background:#1e2235;border-radius:8px;padding:11px;border-left:3px solid ${pc[t]}"><div style="font-size:.64rem;font-weight:700;text-transform:uppercase;color:${pc[t]};margin-bottom:4px">${t}${plan[t]?' — '+plan[t].hours.slice(0,3).map(h=>h+':00').join(', '):''}</div><div class="dim sm">${plan[t]?plan[t].tip:'Complete more quizzes'}</div></div>`).join('');
    $('RL').innerHTML=rs.length?rs.map(s=>{const p=Math.round(s.sc/Math.max(1,s.tot)*100);return`<div class="card row between" style="margin-bottom:7px;padding:11px 14px"><div><span style="font-weight:600;font-size:.83rem">${s.s}</span><span class="dim sm"> — ${s.t}</span>${s.mode&&s.mode!=='normal'?`<span style="font-size:.68rem;background:#2d3148;padding:2px 7px;border-radius:10px;margin-left:5px">${s.mode}</span>`:''}</div><div class="row" style="gap:6px"><span class="lbadge ${s.lv==='bright'?'lb-b':s.lv==='mid'?'lb-m':'lb-l'}">${s.lv}</span><span style="font:700 .84rem monospace">${p}%</span></div></div>`}).join(''):'<div class="dim sm">No sessions yet.</div>';
  }catch(e){}
}

async function genSched(){
  $('schBtn').disabled=true;SH('schLoad');H('schContent');
  try{
    const r=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:ST.uid,exam:ST.exam||'NEET'})});
    const d=await r.json();if(!r.ok)throw new Error(d.detail);
    H('schLoad');const tc={peak:'#7c6fff',mid:'#f59e0b',low:'#f87171'};
    $('schContent').innerHTML=(d.schedule||[]).map(day=>`<div style="margin-bottom:16px"><div style="font-weight:700;font-size:.87rem;margin-bottom:7px;color:#a5b4fc">${day.day}</div>${(day.slots||[]).map(s=>`<div class="ss"><div class="st">${s.time}</div><div class="sd" style="background:${tc[s.type]||'#2d3148'}"></div><div style="flex:1"><div style="font-weight:600;font-size:.82rem">${s.subject} — ${s.topic}</div><div class="dim" style="font-size:.74rem">${s.activity}</div></div><button class="btn bp" style="font-size:.7rem;padding:4px 9px" onclick="qPractice('${s.subject}','${s.topic}')">Start</button></div>`).join('')}</div>`).join('');
    if(d.weak_topics&&d.weak_topics.length)$('schContent').innerHTML+=`<div class="card mt" style="border-color:rgba(248,113,113,.3)"><div style="font-size:.73rem;font-weight:700;color:#f87171;margin-bottom:7px">Priority Focus Areas</div>${d.weak_topics.map(t=>`<div class="dim sm" style="margin-bottom:3px">⚠️ ${t}</div>`).join('')}</div>`;
    SH('schContent');
  }catch(e){H('schLoad');toast('Schedule failed: '+e.message,'er');}
  $('schBtn').disabled=false;
}

async function sendChat(useWrong=false){
  const inp=$('CI'),m=inp.value.trim();if(!m)return;inp.value='';
  const msgs=$('CM');
  msgs.innerHTML+=`<div style="align-self:flex-end;background:#7c6fff;border-radius:13px;border-bottom-right-radius:3px;padding:10px 14px;font-size:.83rem;max-width:85%;color:#fff">${m.replace(/</g,'&lt;')}</div>`;
  const bot=document.createElement('div');bot.style.cssText='align-self:flex-start;background:#1a1d27;border:1px solid #2d3148;border-radius:13px;border-bottom-left-radius:3px;padding:10px 14px;font-size:.83rem;max-width:85%;color:#64748b;line-height:1.6';bot.textContent='Thinking…';msgs.appendChild(bot);msgs.scrollTop=msgs.scrollHeight;
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:ST.uid,message:m,context:`exam:${ST.exam},level:${ST.lv},subject:${ST.subj||''}`,wrong_q:useWrong?ST.lastWQ:'',wrong_a:useWrong?ST.lastWA:''})});
    const d=await r.json();bot.textContent=d.reply||'No reply';bot.style.color='#e2e8f0';
  }catch(e){bot.textContent='Server error.';}
  msgs.scrollTop=msgs.scrollHeight;
}

// ── UPLOAD ───────────────────────────────────────────────
function loadUpSubjs(){
  const exam=ST.exam||'NEET';
  const subjs=Object.keys(SUBJS[exam]||SUBJS.NEET);
  const sel=$('upSubj');
  if(sel) sel.innerHTML=subjs.map(s=>`<option value="${s}">${s}</option>`).join('');
}
function readUpFile(e){
  const f=e.target.files[0];if(!f)return;
  const rd=new FileReader();
  rd.onload=ev=>{$('upText').value=ev.target.result.slice(0,5000);toast('File loaded ✓','ok');};
  rd.readAsText(f);
}
async function genUploadMCQ(){
  const text=$('upText').value.trim();
  if(!text){toast('Paste your notes first','er');return;}
  const subj=$('upSubj').value||'General';
  $('upBtn').disabled=true;SH('upLoad');H('upRes');
  const fd=new FormData();
  fd.append('file',new Blob([text],{type:'text/plain'}),'notes.txt');
  fd.append('exam',ST.exam||'NEET');
  fd.append('subject',subj);
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Upload failed');
    window._uqs=d.questions;
    $('upRes').innerHTML=`
      <div class="stt">${d.questions.length} Questions Generated from Your Notes</div>
      ${d.questions.map((q,i)=>`
        <div class="card" style="margin-bottom:9px;padding:14px">
          <div style="font-weight:600;font-size:.85rem;margin-bottom:7px">Q${i+1}: ${q.question.replace(/</g,'&lt;')}</div>
          <div class="dim sm">${q.options.join(' · ')}</div>
          <div style="color:#10b981;font-size:.73rem;margin-top:5px">✓ ${q.correct}</div>
        </div>`).join('')}
      <button class="btn bp bw mt" onclick="startUploadQuiz()">Start Quiz with These Questions →</button>`;
    SH('upRes');
  }catch(e){toast('Failed: '+e.message,'er');}
  H('upLoad');$('upBtn').disabled=false;
}
function startUploadQuiz(){
  const qs=window._uqs||[];if(!qs.length){toast('No questions generated','er');return;}
  const subj=$('upSubj').value||'General';
  ST.qs=qs;ST.qi=0;ST.sc=0;ST.times=[];ST.streak=0;ST.cs=0;ST.ws=0;ST.totalAns=0;
  ST.subj=subj;ST.top='Upload';ST.sid=Date.now();ST.running=true;ST.mode='normal';ST.lv='mid';
  // Switch to quiz page and show step 3 directly
  [0,1,2,3,4,5,6].forEach(n=>{const p=$('P'+n);if(p){p.classList.add('H');if(n===6)p.style.display='none';}});
  $('P1').classList.remove('H');
  document.querySelectorAll('.nl').forEach((b,n)=>n===1?b.classList.add('on'):b.classList.remove('on'));
  gostep(3);
  $('HI').textContent=subj+' — Uploaded Notes';
  SH('normHUD');H('examHUD');
  H('QL');SH('QD');
  updLvBadge();initFAT();showQ();
  toast('Quiz started from your notes! 📄','ok');
}

// Drag and drop for upload
document.addEventListener('DOMContentLoaded',function(){
  const dz=document.getElementById('upDrop');
  if(dz){
    dz.addEventListener('dragover',e=>{e.preventDefault();dz.style.borderColor='#7c6fff';});
    dz.addEventListener('dragleave',()=>dz.style.borderColor='#2d3148');
    dz.addEventListener('drop',e=>{
      e.preventDefault();dz.style.borderColor='#2d3148';
      const f=e.dataTransfer.files[0];if(!f)return;
      const rd=new FileReader();rd.onload=ev=>{$('upText').value=ev.target.result.slice(0,5000);toast('File loaded ✓','ok');};rd.readAsText(f);
    });
  }
});

// ── FATIGUE ──────────────────────────────────────────────
document.addEventListener('keydown',e=>{const n=Date.now();if(FAT.lk>0){const iv=n-FAT.lk;if(iv<5000){FAT.kt.push(iv);if(FAT.kt.length>20)FAT.kt.shift();}}FAT.lk=n;});
document.addEventListener('scroll',()=>{FAT.sp.push({pos:window.scrollY,t:Date.now()});if(FAT.sp.length>30)FAT.sp.shift();},true);
function trackAC(idx){if(FAT.li!==-1&&FAT.li!==idx)FAT.ac++;FAT.li=idx;}
function avg2(a){return a.length?a.reduce((x,y)=>x+y)/a.length:0;}
function stdD(a){const m=avg2(a);return Math.sqrt(a.reduce((x,y)=>x+(y-m)**2,0)/a.length);}
function kScore(){if(FAT.kt.length<5)return 0;const f=avg2(FAT.kt.slice(0,3)),l=avg2(FAT.kt.slice(-3));const cv=stdD(FAT.kt)/(avg2(FAT.kt)||1);let s=0;if(l>f*1.4)s+=2;if(cv>0.8)s+=1;return s;}
function scScore(){if(FAT.sp.length<3)return 0;let rev=0;for(let i=2;i<FAT.sp.length;i++){const d1=FAT.sp[i-1].pos-FAT.sp[i-2].pos,d2=FAT.sp[i].pos-FAT.sp[i-1].pos;if(d1*d2<0)rev++;}return rev>5?2:rev>2?1:0;}
function acScore(){const cr=FAT.ac/Math.max(1,ST.times.length);return cr>1.5?2:cr>0.8?1:0;}
function tdScore(){const h=new Date().getHours();const pk=window._ph||[];const mi=window._mh||[];return pk.includes(h)?0:mi.includes(h)?1:2;}
function bScore(){if(!FAT.wa||FAT.br===0)return 0;let s=0;if(FAT.br>25)s+=3;else if(FAT.br>20)s+=1;else if(FAT.br<8)s+=2;if(FAT.eo<0.5)s+=3;else if(FAT.eo<0.7)s+=1;return s;}

function calcFAT(){
  const sigs={};let total=0;
  let rt=0;if(ST.times.length>=3){const f=avg2(ST.times.slice(0,3)),l=avg2(ST.times.slice(-3));if(l>f*1.5)rt=3;else if(l>f*1.2)rt=1;}
  sigs['⏱ Response']=rt;total+=rt;
  const es=Math.min(3,ST.streak);sigs['❌ Streak']=es;total+=es;
  const ks=kScore();sigs['⌨️ Typing']=ks;total+=ks;
  const sc2=scScore();sigs['📜 Scroll']=sc2;total+=sc2;
  const ac=acScore();sigs['🔄 Changes']=ac;total+=ac;
  const td=tdScore();sigs['🕐 Time']=td;total+=td;
  const bs=bScore();sigs['👁 Blink']=bs;total+=bs;
  const sf={'sharp':0,'okay':1,'tired':3}[ST.sf]||0;if(ST.sf)sigs['😊 Self']=sf;total+=sf;
  FAT.score=total;FAT.sigs=sigs;return{total,sigs};
}

function updFAT(){
  const{total}=calcFAT();
  const dots=$('FD').querySelectorAll('.fd');
  dots.forEach((d,i)=>{d.className='fd';if(i<Math.min(5,Math.round(total/2))){if(total>=8)d.classList.add('r');else if(total>=5)d.classList.add('y');else d.classList.add('g');}});
  const fl=$('FL');if(fl){if(total>=8){fl.textContent='😴 Tired';fl.style.color='#f87171';}else if(total>=5){fl.textContent='😐 Tiring';fl.style.color='#f59e0b';}else{fl.textContent='😊 Fresh';fl.style.color='#10b981';}}
  const wfs=$('WFS');if(wfs){wfs.textContent=Math.max(0,100-total*8)+'%';wfs.style.color=total>=8?'#f87171':total>=5?'#f59e0b':'#10b981';}
  return total;
}

async function chkFAT(){
  const score=updFAT();
  try{await fetch('/api/fatigue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:ST.sid,user_id:ST.uid,times:ST.times,streak:ST.streak,total:ST.times.length,ks:kScore(),ss:scScore(),bs:bScore(),eo:FAT.eo,sf:ST.sf})});}catch(e){}
  if(score>=5)showFAT(FAT.sigs);
}

async function showFAT(sigs){
  const sigHtml=Object.entries(sigs||{}).map(([k,v])=>`<div style="display:flex;justify-content:space-between;padding:3px 0"><span>${k}</span><span style="color:${v>=2?'#f87171':v>=1?'#f59e0b':'#10b981'};font-weight:700">${['○○○','●○○','●●○','●●●'][Math.min(3,v)]}</span></div>`).join('');
  $('fatSigs').innerHTML=`<div style="font-size:.7rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-bottom:7px">Fatigue Signals</div>${sigHtml}`;
  const opts=[{title:'Meditation',icon:'🧘',url:'https://www.youtube.com/embed/inpok4MKVLM'},{title:'Breathing',icon:'🌬️',url:'https://www.youtube.com/embed/uxayUBd6T7M'},{title:'Stretch',icon:'🤸',url:'https://www.youtube.com/embed/tAUf7aajBWE'}];
  $('RG').innerHTML='';opts.forEach(o=>{const d=document.createElement('div');d.className='tile';d.style.padding='12px 7px';d.innerHTML=`<div style="font-size:1.5rem;margin-bottom:4px">${o.icon}</div><div style="font-size:.74rem;font-weight:700">${o.title}</div>`;d.onclick=()=>startRec(o.title,o.url);$('RG').appendChild(d);});
  $('FOV').style.display='flex';
}
function startRec(t,u){$('FOV').style.display='none';$('VT').textContent=t;$('VF').src=u+'?autoplay=1';ST.rsecs=300;$('VOV').style.display='flex';clearInterval(ST.rint);ST.rint=setInterval(()=>{ST.rsecs--;$('VC').textContent=Math.floor(ST.rsecs/60)+':'+(ST.rsecs%60).toString().padStart(2,'0');if(ST.rsecs<=0)endRec();},1000);}
function extRec(){ST.rsecs+=600;toast('+10 min','ok');}
function endRec(){clearInterval(ST.rint);$('VOV').style.display='none';$('VF').src='';ST.streak=0;updFAT();toast('Welcome back 💪','ok');}
function skipRec(){$('FOV').style.display='none';}

// ── WEBCAM ──────────────────────────────────────────────
function initFAT(){FAT.kt=[];FAT.lk=0;FAT.sp=[];FAT.ac=0;FAT.li=-1;FAT.br=0;FAT.eo=1.0;FAT.bw=[];FAT.cf=0;FAT.score=0;initCam();}
async function initCam(){
  SH('WCV');
  try{
    FAT.s=await navigator.mediaDevices.getUserMedia({video:{width:320,height:240,facingMode:'user'}});
    $('WCS').style.background='#f59e0b';
    // Create video element and ADD it to the WCV panel so user can see it
    const vid=document.createElement('video');
    vid.autoplay=true;vid.playsInline=true;vid.muted=true;
    vid.srcObject=FAT.s;
    vid.style.cssText='width:100%;border-radius:7px;margin-bottom:5px;display:block';
    // Insert video before canvas
    const wcc=$('WCC');
    wcc.parentNode.insertBefore(vid, wcc);
    FAT.vid=vid; // store reference for canvas drawing
    await new Promise(res=>{vid.onloadedmetadata=res;});
    await vid.play().catch(()=>{});
    FAT.wa=true;
    $('WCS').style.background='#10b981';
    loadFM(vid);
  }
  catch(e){
    $('WCS').style.background='#f87171';
    $('WCE').textContent=e.name==='NotAllowedError'?'Camera denied - allow camera access':'No camera found';
    SH('WCE');FAT.wa=false;
  }
}
function loadFM(vid){
  const s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/face_mesh.js';s.crossOrigin='anonymous';
  s.onload=()=>{try{const fm=new FaceMesh({locateFile:f=>`https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${f}`});fm.setOptions({maxNumFaces:1,refineLandmarks:true,minDetectionConfidence:.5,minTrackingConfidence:.5});fm.onResults(onFM);FAT.fm=fm;(async function frame(){if(!FAT.wa||!FAT.fm)return;try{await FAT.fm.send({image:vid});}catch(e){}requestAnimationFrame(frame);})();}catch(e){simBlink(vid);}};
  s.onerror=()=>simBlink(vid);document.head.appendChild(s);
}
function onFM(res){
  const cv=$('WCC'),ctx=cv.getContext('2d'),W=cv.width,H2=cv.height;
  ctx.clearRect(0,0,W,H2);
  // Draw live video feed on canvas
  if(FAT.vid && FAT.vid.readyState>=2){
    ctx.drawImage(FAT.vid,0,0,W,H2);
  }
  if(!res.multiFaceLandmarks||!res.multiFaceLandmarks.length){
    ctx.fillStyle='rgba(100,116,139,.8)';ctx.font='10px monospace';ctx.fillText('No face detected',4,H2/2);
    return;
  }
  const lm=res.multiFaceLandmarks[0];
  function dist(a,b){return Math.sqrt((lm[a].x-lm[b].x)**2+(lm[a].y-lm[b].y)**2);}
  const ear=((dist(159,145)+dist(158,153))/(2*dist(33,133)+.001)+(dist(386,374)+dist(385,380))/(2*dist(362,263)+.001))/2;
  FAT.eo=Math.min(1,ear/0.25);
  if(ear<0.21){
    FAT.cf++;
    // If eyes closed for 90+ frames (~3 seconds at 30fps) → trigger fatigue immediately
    if(FAT.cf===90){
      console.log('Eyes fully closed 3s — triggering fatigue');
      showFAT({'👁 Eyes Closed':3,'⏱ Response':0,'❌ Streak':0});
    }
  }
  else{
    if(FAT.cf>=2&&FAT.cf<=15){FAT.bw.push(Date.now());}
    FAT.cf=0;
  }
  const now=Date.now();FAT.bw=FAT.bw.filter(t=>now-t<60000);FAT.br=FAT.bw.length;
  $('WBR').textContent=FAT.br+'/min';$('WBR').style.color=FAT.br>25||FAT.br<8?'#f87171':'#10b981';
  $('WEO').textContent=Math.round(FAT.eo*100)+'%';$('WEO').style.color=FAT.eo<0.6?'#f87171':'#10b981';
  // Draw eye landmark dots on top of video
  const eyePts=[33,133,159,145,158,153,160,161,362,263,386,374,385,380,387,388];
  eyePts.forEach(i=>{
    if(!lm[i])return;
    ctx.beginPath();ctx.arc(lm[i].x*W,lm[i].y*H2,2.5,0,Math.PI*2);
    ctx.fillStyle=FAT.eo<0.5?'#f87171':FAT.eo<0.7?'#f59e0b':'#10b981';ctx.fill();
  });
  // EAR and blink overlay
  ctx.fillStyle='rgba(0,0,0,.6)';ctx.fillRect(0,H2-22,W,22);
  ctx.fillStyle='#e2e8f0';ctx.font='bold 9px monospace';
  ctx.fillText('EAR:'+ear.toFixed(3)+'  Blinks:'+FAT.bw.length+'  '+Math.round(FAT.eo*100)+'%',4,H2-8);
}
function simBlink(vid){
  const cv=document.createElement('canvas');cv.width=64;cv.height=48;const ctx=cv.getContext('2d');
  let last=255,det=false,darkFrames=0;FAT.wa=true;
  $('WCS').style.background='#f59e0b';$('WCE').textContent='Fallback blink mode';SH('WCE');
  function loop(){
    if(!FAT.wa)return;
    try{ctx.drawImage(vid,0,0,64,48);}catch(e){setTimeout(loop,100);return;}
    const data=ctx.getImageData(0,0,64,48).data;
    let b=0;for(let i=0;i<data.length;i+=4)b+=data[i];b/=(data.length/4);
    FAT.eo=Math.min(1,b/200); // rough eye open estimate from brightness
    $('WEO').textContent=Math.round(FAT.eo*100)+'%';
    // Blink detection
    if(last-b>15&&!det){FAT.bw.push(Date.now());det=true;}
    else if(last-b<5)det=false;
    last=b;
    // Dark frame = eyes closed
    if(b<80){darkFrames++;}else{darkFrames=0;}
    if(darkFrames===30){showFAT({'👁 Eyes Closed (3s)':3});}// ~3s at 10fps
    const now=Date.now();FAT.bw=FAT.bw.filter(t=>now-t<60000);FAT.br=FAT.bw.length;
    $('WBR').textContent=FAT.br+'/min';
    setTimeout(loop,100);
  }loop();
}
function stopCam(){FAT.wa=false;if(FAT.s){FAT.s.getTracks().forEach(t=>t.stop());FAT.s=null;}H('WCV');}
</script></body></html>"""


if __name__ == "__main__":
    import uvicorn
    print("\\n" + "="*52)
    print("  Personal AI Coach — Complete Working Edition")
    print("="*52)
    k = os.environ.get("GROQ_API_KEY","")
    if k: print(f"  ✅ Groq key loaded ({k[:14]}...)")
    else: print("  ⚠️  No GROQ_API_KEY!  Run: $env:GROQ_API_KEY=gsk_...")
    print("  🌐 Open: http://localhost:8000")
    print("="*52 + "\\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
