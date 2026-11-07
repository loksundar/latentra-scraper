"""
Validation pipeline — rejects items missing required fields,
non-IT jobs, non-US jobs, and jobs older than 90 days.
Tracks metrics for scrape_runs logging.
"""

import re
import logging
from datetime import datetime, timedelta

from scrapy.exceptions import DropItem

from latentra_scraper.schema import MAX_LEN_IDENTITY

logger = logging.getLogger(__name__)


# ── Title filter ──────────────────────────────────────────────────────
# The board keeps technical roles (software / IT / infrastructure) with full
# coverage of data, analytics, BI, ML and AI work, and drops non-technical
# ones. Three tiers, evaluated by is_non_it_title():
#
#   1. HARD_NON_IT_TITLE_PATTERNS — the title's role noun is unambiguously
#      non-technical (account executive, recruiter, financial analyst,
#      counsel...). Dropped outright; no override, so "Account Executive,
#      Data Engineering" and "Recruiter, Engineering" cannot be rescued by
#      the technology they sell or hire for.
#   2. NON_IT_TITLE_PATTERNS — a non-technical signal (sales, marketing,
#      finance, HR, clinical, trades, or a management noun like manager /
#      director / coordinator / specialist). Dropped unless tier 3 matches.
#   3. IT_TITLE_PATTERNS — the title's actual role noun is technical
#      (engineer, developer, analyst, architect, administrator, data /
#      analytics / AI / ML phrases, IT support...). Overrides tier 2 only.
#      Domain and tool words on their own (ai, ml, cloud, aws, saas, python,
#      salesforce) deliberately do NOT rescue, because they appear in
#      business titles ("Account Executive, AI", "Marketing Manager, AI
#      Products", "Sales Manager - SaaS").
#
# Titles that match none of the tiers are kept ("Software Engineer",
# "Data Scientist", "Intern").
#
# Regex notes: every alternative sits between \b anchors, so a bare stem can
# never match a longer word ("product design" did not match "Product
# Designer"; a stem followed by the group's closing \b never matches
# "radiologist" — prefix stems need \w*). Alternatives must END on a word
# character for the closing \b to hold; use lookaheads for trailing
# punctuation / end-of-string conditions.

# Tier 1 — hard drops (case-insensitive). No override.
HARD_NON_IT_TITLE_PATTERNS = re.compile(
    r'\b('
    # Sales — the role noun is the seller, whatever the product
    r'account\s*(?:executive|exec)s?|account\s*directors?|'
    r'(?<!technical )(?<!technical-)(?<!tech )account\s*managers?|'
    r'sales\s*(?:manager|director|representative|rep|reps|associate|executive|exec|consultant|assistant|'
    r'agent|advisor|trainee|intern|professional|coordinator|enablement|leader|lead|head|officer|'
    r'specialist|development(?!\s*engineer)|team\s*lead|account|strategist|planner)s?|'
    # "Director of Sales", "Senior Manager, Sales" — head noun at the start
    # of the title or after a separator; "Data Science Manager, Sales" is
    # a data role and does not match.
    r'(?:^|,\s*|-\s*|–\s*)(?:(?:senior|sr\.?|regional|area|global|national|district|associate|assistant|'
    r'executive|group|deputy)\s+)*(?:head|director|vp|vice\s*president|manager|chief|svp|evp|avp)'
    r'\s*(?:of\s*|,\s*|-\s*)?sales(?!\s*(?:engineer|analytics|systems?|technology|tech|data|intelligence|'
    r'operations\s*analyst|ops\s*analyst))|'
    r'sdrs?(?!\s*(?:hardware|software|radio|engineer))|bdrs?|business\s*development(?!\s*engineer)|biz\s*dev|'
    r'customer\s*success\s*(?:manager|associate|specialist|lead|director|representative|rep|advocate|partner|'
    r'executive|coordinator|intern|head|consultant|team\s*lead)s?|'
    r'(?:^|,\s*|-\s*|–\s*)(?:(?:senior|sr\.?|global|regional|associate|group)\s+)*'
    r'(?:head|director|vp|vice\s*president)\s*(?:of\s*|,\s*)?customer\s*success(?!\s*engineer)|'
    # Recruiting / HR / people
    r'recruiters?|recruit(?:ing|ment)\s*(?:manager|coordinator|lead|partner|specialist|associate|director|'
    r'assistant|intern|consultant|team)s?|'
    r'(?:technical|engineering|technology|tech|it|software|sales|gtm|executive|corporate|campus|university|'
    r'early\s*career|product|design|clinical)\s*recruit(?:ing|ment)|'
    r'(?:head|director|vp|vice\s*president|manager|lead)\s*(?:of\s*|,\s*|-\s*)?(?:\w+\s+)?recruit(?:ing|ment)|'
    r'recruit(?:ing|ment)\s*(?:operations|ops|programs?)(?!\s*(?:analyst|engineer|data|systems?|technology|'
    r'analytics|developer))|'
    r'sourcers?|'
    r'talent\s*(?:acquisition|partner|sourcer|scout|coordinator|advisor|manager|lead|director|team|specialist|'
    r'associate|programs?)s?|'
    r'talent\s*(?:management|development|operations|ops|strategy)(?!\s*(?:system|analyst|data|technology|tech|'
    r'engineer|analytics))|'
    r'hr\s*(?:manager|director|generalist|coordinator|specialist|partner|business\s*partner|assistant|lead|head|'
    r'intern|associate|advisor|consultant|administrator|representative|rep|officer|analyst)s?|'
    r'hr\s*(?:operations|ops)(?!\s*(?:analyst|engineer|data|systems?|technology|analytics|developer))|'
    r'hrbp|human\s*resources(?!\s*(?:information|systems?|technology|data|analytics|engineer))|'
    r'people\s*(?:partner|business\s*partner|manager|lead|director|generalist|coordinator|specialist|associate|'
    r'head|advisor|consultant|relations)s?|'
    r'people\s*(?:ops|operations|programs?|experience)(?!\s*(?:analyst|engineer|data|systems?|technology|'
    r'analytics|developer|scientist))|'
    r'payroll\s*(?:specialist|manager|coordinator|administrator|lead|associate|clerk|accountant|supervisor|'
    r'director|processor|analyst)s?|'
    r'executive\s*assistants?|administrative\s*assistants?|receptionists?|chief\s*of\s*staff|'
    r'data\s*(?:entry|review)|'
    # Marketing — adjacent role noun
    r'marketing\s*(?:manager|director|coordinator|specialist|associate|assistant|lead|head|vp|executive|'
    r'consultant|generalist|strategist|representative|rep|officer|partner|planner|producer|'
    r'program\s*manager|project\s*manager|automation\s*(?:manager|specialist))s?|'
    r'marketing\s*(?:operations|ops)(?!\s*(?:analyst|engineer|data|systems?|technology|developer|analytics))|'
    r'product\s*marketing(?!\s*engineer)|'
    r'brand\s*(?:manager|marketing|strategist|ambassador|partnerships?|director|lead|specialist|designer|'
    r'associate|coordinator|representative|rep)s?|'
    # Finance / accounting / legal analysts and managers
    r'financ(?:e|ial)\s*(?:planning|reporting|operations|planning\s*(?:&|and)\s*analysis)?\s*analysts?|'
    r'(?:tax|benefits?|compensation|accounting|treasury|fp&a|investment|equity\s*research|budget|cost|'
    r'contracts?|legal|legal\s*operations|legal\s*ops|recruiting|talent|staffing|imagery|intel|all[-\s]?source|'
    r'liquidity|controllers?|product\s*control|valuation|credit(?!\s*risk)|loan|mortgage|banking|wholesale|'
    r'asset\s*management|funds?|securities|capital\s*markets?|markets|investment\s*banking|wealth\s*management|'
    r'private\s*bank\w*|commercial\s*banking|corporate\s*banking|'
    r'targeting|real\s*estate|leasing|claims|underwriting|billing|collections|commissions?|grants?|fundraising|'
    r'regulatory|government\s*relations|public\s*affairs|pr|investor\s*relations|m&a|'
    r'behavior(?:al)?|lab|laboratory|qc|quality\s*control|chemistry|microbiology)\s*analysts?|'
    r'(?<!unified )(?<!data )communications?\s*analysts?|'
    r'(?:banking|real\s*estate|markets|securities|wealth|treasury)\s*(?:program\s*|full[-\s]*time\s*|summer\s*)?analysts?|'
    r'legal\s*(?:operations|ops)|'
    r'(?:^|[,\-–—/|(]\s*)(?:(?:senior|sr\.?|junior|jr\.?|lead|principal|staff|associate)\s+)*analysts?'
    r'\s*(?:i{1,3}|iv|v|\d+)?\s*[-–—,(]\s*(?:\w+\s+){0,2}?(?:finance|financial|accounting|tax|payroll|benefits|compensation|'
    r'treasury|fp&a|hr|human\s*resources|people|talent|recruiting|legal|litigation|contracts?|procurement|'
    r'purchasing|audit|compliance|regulatory|government\s*relations|public\s*affairs|communications?|pr|'
    r'marketing(?!\s*(?:analytics|data|science|technology|systems|operations\s*analyst))|'
    r'sales(?!\s*(?:analytics|data|systems|operations|ops|engineering|technology))|'
    r'real\s*estate|investment|equity\s*research|banking|credit|loan|mortgage|wealth|'
    r'claims|underwriting|billing|collections|grants?|fundraising|m&a|corporate\s*development|'
    r'investor\s*relations)\b|'
    r'investor\s*(?:reporting|relations|services)|contracts?\s*(?:manager|administrator|specialist|negotiator)s?|'
    r'(?<!business )(?<!artificial )(?<!threat )(?<!market )(?<!competitive )(?<!cyber )(?<!data )(?<!bi )'
    r'intelligence\s*analysts?|'
    r'(?<!it )(?<!security )(?<!technology )(?<!technical )(?<!cyber )(?<!cloud )(?<!data )(?<!privacy )(?<!soc )'
    r'compliance\s*analysts?|'
    r'(?<!it )(?<!technology )(?<!systems )(?<!system )(?<!technical )(?<!data )audit\s*analysts?|'
    r'bcba|'
    r'cost\s*(?:control(?!\s*engineer)|accountant|estimator|estimating)|'
    r'(?:cost\s*(?:and|&)\s*schedule|schedule\s*control|earned\s*value|evms)(?!\s*(?:\w+\s+)?engineer)\s*\w*|'
    r'finance\s*(?:manager|director|lead|associate|partner|business\s*partner|assistant|coordinator|intern|head|'
    r'vp|specialist|controller|officer)s?|'
    r'financial\s*(?:planning|controller|accountant|advisor|consultant|representative|services\s*representative)|'
    r'fp&a|accountants?|bookkeep\w*|'
    r'counsel(?:l?or)?s?|attorneys?|lawyers?|paralegals?|law\s*clerks?|litigation|'
    # Design roles that are visual / brand / L&D, not product or UX
    r'(?:graphic|motion|brand|marketing|creative|print|packaging|fashion|interior|instructional|'
    r'(?<!machine )(?<!deep )learning(?:\s*experience)?|apparel|textile|jewelry|floral|kitchen|landscape|exhibit|presentation|'
    r'communications?)\s*design\w*|'
    r'landscape\s*architect\w*|construction\s*(?:project\s*)?manag\w*|'
    r'cnc(?!.*\bengineer)|'
    r'instructional\s*(?:\w+\s+)?design\w*|experiential\s*design\w*|'
    # CAD / drafting designers (buildings, piping, mechanical) — not product / UX
    r'(?:electrical|civil|architectural|structural|mechanical|plumbing|piping|process\s*piping|hvac|cad|'
    r'industrial|furniture|lighting|stage|costume|bath|cabinet|signage|environmental\s*graphic)\s*designers?|'
    r'analyst\s*relations|'
    r'finance\s*(?:&|and)\s*\w+\s*analysts?|'
    r'client\s*(?:manager|director|partner|executive|lead|relationship\s*manager)s?|'
    # Wet-lab scientists: bench-science markers with no data / ML signal
    r'(?!.*\b(?:data|ml|ai|machine\s*learning|computational|bioinformatic\w*|informatics|statistic\w*|software|'
    r'algorithm\w*|imaging|modeling|modelling|simulation|digital|automation)\b)'
    r'(?:elisa|assay|antibod\w*|protein|in[-\s]vivo|in[-\s]vitro|cell\s*(?:culture|biology|line)|'
    r'molecular\s*biology|biochem\w*|organic\s*chemistry|analytical\s*chemistry|medicinal\s*chemistry|'
    r'formulation|pharmacolog\w*|toxicolog\w*|immunolog\w*|microbiolog\w*|virolog\w*|histolog\w*|pathology|'
    r'wet[-\s]lab|bench|method\s*development|process\s*development|upstream|downstream|purification)'
    r'\s*(?:\w+\s+){0,4}?scientists?|'
    # Healthcare / clinical roles that are never IT
    r'medical\s*(?:coder|coding|biller|billing|scribe|assistant|director|affairs|science\s*liaison|writer|'
    r'receptionist|records|office|secretary|technologist|laboratory|lab)|'
    r'(?:ct|mri|surgical|cardiac|cardiovascular|vascular|nuclear\s*medicine|ultrasound|x-?ray|mammography|'
    r'cath\s*lab|catheterization|endovascular|polysomnograph\w*|respiratory|echo|sterile\s*processing|'
    r'histolog\w*|cytolog\w*|lab(?:oratory)?)\s*technologists?|'
    r'speech[-\s]*language\s*patholog\w*|'
    # Trades / guards — never IT
    r'electrician|plumber|carpenter|welder|janitor|custodian|forklift|truck\s*driver|cdl|delivery\s*driver|'
    r'cashier|stocker|'
    r'(?<!information )(?<!cyber )(?<!it )(?<!systems )(?<!system )(?<!chief )(?<!data )(?<!technology )'
    r'(?<!network )(?<!cloud )(?<!product )(?<!application )(?<!software )'
    r'security\s*(?:guard|officer|monitor|patrol|screener)s?|armed\s*guard|'
    r'psychological\s*operations|psyop\w*|'
    # Other clearly non-IT
    r'idos|lame'
    r')\b',
    re.IGNORECASE,
)

# Tier 1a — role nouns that are unambiguous wherever they sit in the title
# (a title containing "Recruiter" or "Attorney" is that role). Beat the
# strong data tier unless the head itself names a technical role.
ANYWHERE_NON_IT_TITLE_PATTERNS = re.compile(
    r'\b('
    r'recruiters?|sourcers?|talent\s*acquisition|hr\s*business\s*partners?|hrbp|people\s*(?:business\s*)?partners?|'
    r'attorneys?|counsel(?:l?or)?s?|lawyers?|account\s*(?:executive|exec)s?|sales\s*(?:executive|exec|representative|rep)s?|'
    r'(?:head|director|vp|vice\s*president|manager|lead|associate),?\s*(?:of\s*)?business\s*development'
    r')\b',
    re.IGNORECASE,
)

# Tier 1b — context-dependent hard drops: a non-technical function's
# programme / project management, and bank "Analyst Program" intakes. Dropped
# unless the title carries a strong data signal (tier 3a).
CONTEXT_NON_IT_TITLE_PATTERNS = re.compile(
    r'\b('
    r'(?<!technical )(?<!tech )(?<!it )(?<!software )(?<!engineering )'
    r'(?:clinical|medical|construction|facilities|real\s*estate|events?|marketing|hr|people|recruiting|talent|'
    r'training|learning|education|community|sales|partner|channel|supply\s*chain|logistics|procurement|'
    r'manufacturing|production|ehs|regulatory|legal|finance|tax|payroll|benefits|grants?|fundraising|'
    r'philanthropy|nonprofit)\s*(?:program|programs|project|projects)\s*'
    r'(?:manager|coordinator|lead|director|specialist|associate|administrator|assistant)s?|'
    r'(?<!data )(?<!business )analyst\s*programs?'
    r')\b',
    re.IGNORECASE,
)

# Tier 2 — soft drops (case-insensitive). Dropped unless IT_TITLE_PATTERNS
# matches. Broad on purpose: the override decides by the role noun.
NON_IT_TITLE_PATTERNS = re.compile(
    r'\b('
    # Food / hospitality / retail / guest
    r'cook|chef|baker|bartender|barista|waiter|waitress|busser|dishwasher|'
    r'food\s*service|catering|kitchen|sous\s*chef|line\s*cook|prep\s*cook|restaurant|hotel|hospitality|guest|'
    r'front\s*desk|concierge|valet|housekeep\w*|maid|cleaning|room\s*attendant|attendant|casino|table\s*games|'
    r'slot|lifeguard|'
    r'retail|store|stores|branch|showroom|clerk|teller|merchandis\w*|stock\w*|host|hostess|server|servers|'
    r'loss\s*prevention|asset\s*protection|dealer\w*|territory|'
    # Trades / manufacturing / physical labor
    r'mechanic|hvac|landscap\w*|roofer|mason|painter(?!\s*(?:ui|ux|product|visual))|'
    r'warehouse|material\s*handler|machine\s*(?:operator|shop|builder|technician|tender|maintenance)|machinist|'
    r'assembler|assembly|fabricat\w*|welding|floor\s*tech\w*|operator|operators|opr|oprs|technician|technicians|'
    r'mach\s*tool|tool\s*(?:and|&)\s*die|toolmaker|'
    r'production|manufactur\w*|plant|factory|maintenance|'
    r'quality\s*(?:inspector|control|technician|specialist|manager|supervisor|coordinator|auditor|lead|'
    r'associate|director|systems?)|qc|inspector|inspection|'
    r'driver|drivers|courier|dispatch\w*|fleet|transportation|trucking|freight|shipping|receiving|packag\w*|'
    r'packer|picker|loader|expedit\w*|material\w*|parts|supplier|estimator|estimating|'
    r'delivery\s*(?:expert|associate|driver|rider|partner|courier)|sample\s*(?:processing|management|prep\w*)|'
    r'supply\s*chain|logistics|distribution|fulfil?lment|inventory|procurement|purchasing|sourcing|buyer|'
    r'category\s*manag\w*|planner|planning|scheduler|scheduling|'
    r'construction|laborer|labourer|foreman|superintendent|heavy\s*equipment|crane|'
    r'surveyor(?!\s*(?:software|data))|rigger|apprentice|journeyman|helper|worker|workers|crew|crewperson|shift|'
    r'repair\s*tech\w*|porter|steward|warehouser|pipefitter|locksmith|deburr\w*|busperson|floorperson|fueler|'
    r'promoter|'
    r'aircraft|avionics|a&p|helicopter|automotive|vehicle|engine|engines|workshop|garage|'
    r'ehs|hse|safety|environmental|sustainability|facilities|facility|'
    r'cleanroom|wafer|semiconductor\s*(?:technician|operator)|'
    # Healthcare / life sciences
    r'nurse|nursing|rn|lpn|cna|phlebotom\w*|radiolog\w*|medical\s*assistant|dental|hygienist|pharmacist|'
    r'pharmacy\s*tech|physician|\w*surgeons?|resident\s*physician|residency|therapist(?!\s*(?:ui|ux))|'
    r'paramedic|emt|msl|perfusionist|technologists?|'
    r'physicist|geolog\w*|hydrogeolog\w*|meteorolog\w*|archaeolog\w*|metallurg\w*|geoscien\w*|'
    r'healthcare|health|clinical|clinic|medical|medicine|patient|patients|pharma\w*|pharmacy|'
    r'biolog\w*|chemist\w*|microbiolog\w*|toxicolog\w*|pharmacolog\w*|formulation|bioprocess|'
    r'cell\s*(?:culture|therapy|line)|assay|lab|labs|laboratory|scientist|scientists|'
    r'research\s*(?:associate|assistant|coordinator|technician|nurse)|'
    r'oncology|immunology|neuroscience|hematology|cardiology|dermatology|psychiatry|diagnostic\w*|imaging|'
    r'surgical|surgery|hospital|caregiver|care|therapy|rehab\w*|dietitian|nutrition\w*|veterinar\w*|'
    r'optometr\w*|audiolog\w*|speech[-\s]*language|behavioral\s*health|mental\s*health|social\s*work\w*|'
    r'case\s*manag\w*|chaplain|wellness|fitness|coach|trainer|yoga|massage|esthetic\w*|aesthetic\w*|cosmet\w*|'
    r'salon|spa|beauty|'
    # Education
    r'teacher|teaching|instructor|professor|faculty|lecturer|tutor\w*|educator|education|curriculum|course|'
    r'training|enrollment|admissions|student|academic|school|campus|classroom|librarian|library|'
    # Legal / compliance / policy
    r'legal|law|jd|compliance|regulatory|policy|government\s*(?:affairs|relations)|public\s*(?:policy|affairs)|'
    r'privacy\s*(?:counsel|officer|manager|specialist|program)|'
    # Military / defense operations (non-IT)
    r'humint|sigint|geoint|biometric|fingerprint|intelligence\s*(?:instructor|trainer|collector)|'
    r'combat|infantry|artillery|munitions|armament|ordnance|force\s*protection|'
    r'counterintel\w*|targeting|targeteer|mission\s*(?:planner|planning|assurance|support)|weapons?|'
    r'maintainer|investigator|monitor|intelligence\s*(?:integrator|officer|specialist|professional)|'
    r'firearms?|forensic\w*|pilot|'
    r'ew\s*(?:analyst|operator|specialist)|electronic\s*warfare|linguist|interpreter|translator|'
    # Administrative
    r'administrative|secretary|clerical|front\s*office|office\s*services|personal\s*assistant|'
    r'virtual\s*assistant|assistant|typist|transcription\w*|mailroom|mail\s*clerk|file\s*clerk|records|'
    r'document\s*control|notary|office\s*(?:manager|coordinator|assistant|administrator)|'
    # Business functions at tech companies — not IT roles unless the role
    # noun is technical ("Sales Engineer", "Marketing Data Analyst",
    # "Finance Systems Analyst" stay).
    r'sales|selling|quota|renewals?|account|accounts|'
    r'marketing|marketer|seo|sem|ppc|paid\s*(?:ads|media|search|social)|advertising|'
    r'ads?\s*(?:specialist|manager|coordinator|operations|ops|sales|account)|adops|campaign\w*|brand|branding|'
    r'demand\s*gen\w*|growth|go-to-market|gtm|lifecycle\s*marketing|'
    r'crm\s*(?:manager|specialist|marketing|coordinator|lead|director)|email\s*marketing|affiliate|cpa|'
    r'media|pr|public\s*relations|communications?|comms|social\s*media|photograph\w*|agents?|facilitator|'
    r'community|events?|evangelist|influencer|ambassador|'
    r'content|copywriter|writer|editor|author|journalist|reporter|proofreader|editorial|publisher|publishing|'
    r'publications?|producer|photographer|videographer|video|creator|anchor|news|newscast|broadcast|on-air|'
    r'podcast|studio|artist|animator|illustrator|art\s*director|creative|'
    r'partnerships?|partner|alliances?|channel|business\s*partner|bizops|business\s*operations|strategy|'
    r'strategic|corporate|m&a|executive|management\s*consult\w*|consultant|consulting|engagement|'
    r'client|clients|customer|customers|cx|relationship|'
    r'people|hr|talent|recruit\w*|staffing|workforce|benefits|compensation|total\s*rewards|'
    r'employee\s*(?:relations|experience|engagement)|diversity|dei|onboarding|workplace|'
    r'accounting|controller|treasury|auditor|audit|tax|fp&a|billing|invoic\w*|collections|credit|loan|'
    r'mortgage|banker|banking|wealth|investment|investor|portfolio|trader|trading|brokerage|broker|insurance|'
    r'underwrit\w*|customs|asset\s*(?:servicing|management)|trusts?\s*(?:and|&)\s*estates?|'
    r'risk\s*(?:associate|manager|officer|specialist|director|lead|consultant|control|intern|advisory|'
    r'management|oversight)|'
    r'finance|financial|budget|cost|pricing|revenue|fundrais\w*|grants?|donor|philanthrop\w*|'
    r'operations|ops|operational|project|program|programs|programme|'
    # Management nouns with no technical qualifier
    r'manager|managers|director|directors|lead|leads|leader|leaders|head|supervisor|officer|'
    r'president|vp|svp|evp|avp|chief|gm|'
    r'coordinator|coordinators|specialist|specialists|representative|rep|reps|associate\s*director|'
    r'generalist|administrator|administrators'
    r')\b',
    re.IGNORECASE,
)

# Tier 3a — strong data / AI / engineering signal. The owner's direction is
# "basically all data works": analytics, data-* and the engineer / developer
# role words keep a title whatever management noun heads it ("Manager,
# Analytics and Market Research", "Data Protection Specialist", "Vice
# President, Data Delivery"), and beat a hard match outside the head
# ("Associate Director, Business Development Forecasting & Analytics",
# "Senior Specialist, Paralegal/Data Analyst"). A hard role noun AT the head
# still wins ("Product Marketing, Data Products", "Client Manager - Insights &
# Analytics"). "Data center" is deliberately absent: it is a building.
STRONG_DATA_TITLE_PATTERNS = re.compile(
    r'\b('
    r'analytics?|'
    r'data\s*(?:mining|platforms?|transformation|integrity|protection|privacy|posture|quality|delivery|'
    r'governance|science|scientists?|engineer\w*|analysts?|analysis|architect\w*|steward\w*|modeler|'
    r'modell?ing|warehous\w*|lake\w*|lakehouse|pipelines?|integration|migration|management|insights?|'
    r'visuali[sz]ation|reporting|strategy|strategist|operations|ops|products?|transfer|catalog\w*|'
    r'literacy|label\w*|annotat\w*|solutions?|services|infrastructure|intelligence|mesh|fabric|'
    r'observability|monitoring|security|loss\s*prevention|classification|lineage|ethics|acquisition|risk|'
    r'journalism|journalist|reporter|evangeli\w*|experience|evaluation|'
    r'(?:&|and)\s*(?:ai|analytics|insights|reporting|ml|machine\s*learning|analysis|technology))|'
    r'big\s*data|master\s*data|technical\s*data|data,|'
    r'business\s*intelligence|bi\s*(?:developer|analyst|engineer|architect|specialist|consultant|lead|manager|'
    r'director|reporting)|'
    r'machine\s*learning|deep\s*learning|artificial\s*intelligence|data\s*science|mlops|ml\s*ops|dataops|'
    r'(?:ai|ml|llm|genai|nlp)\s*(?:engineer\w*|scientist|researcher|architect|developer|platform|ops|operations|'
    r'analytics|focus\w*)|'
    r'statistic\w*|biostat\w*|quant\w*\s*(?:analyst|researcher|developer|modeler|scientist|analytics|research)|'
    r'developers?|engineers?|engineering|programmers?|architects?'
    r')\b',
    re.IGNORECASE,
)

# Tier 3 — technical role nouns and phrases (case-insensitive). A title that
# matches here is kept even when a soft non-IT pattern matched.
IT_TITLE_PATTERNS = re.compile(
    r'\b('
    # Role nouns that are technical wherever they appear
    r'engineers?|engineering|developers?|programm(?:er|ers|ing)|architects?|architecture|analysts?|software|'
    r'coder|coding|sysadmin|dba|sre|devops|dev\s*ops|devsecops|secops|sec\s*ops|mlops|ml\s*ops|dataops|'
    r'data\s*ops|aiops|finops|gitops|noc|sdes?|swes?|sdm|seit|cyber\w*|eng|engr|engrs|is&t|'
    r'evaluation\s*scien\w*|'
    r'systems?\s*development|'
    r'agent\s*(?:development|engineer\w*|platform|infrastructure|architecture|builder|ops|operations|evaluation|'
    r'quality|systems?|framework\w*|tooling)|'
    r'quantum\s*(?:physicist|computing|hardware|software|algorithm\w*|error|information|control|systems?|engineer\w*)|'
    r'(?:ml|ai|data|cloud)\s*infra\w*|infra\s*(?:engineer|eng|team|services?|platform)|'
    # Technical scope named at the end of the title or before a separator:
    # "Delivery Consultant - AI/ML, Professional Services", "Consultant, Data"
    r'(?:data|analytics|ai|ml|ai/ml|ml/ai|machine\s*learning|data\s*science|genai|gen\s*ai|generative\s*ai|llms?|'
    r'nlp|computer\s*vision|devops|cloud\s*infrastructure)(?=\s*(?:[,\-–—/|)&+]|$))|'
    r'(?:ai|ml|genai|llm|nlp|data|analytics|machine\s*learning)\s*[/&+]\s*'
    r'(?:ai|ml|genai|llm|nlp|data|analytics|machine\s*learning|insights)|'
    r'delivery\s*consultant|professional\s*services\s*consultant|proserve|'
    r'(?:technology|it|technical|data|analytics|cloud|security|cyber\w*|solutions?|digital\s*trust|erp|sap|'
    r'salesforce|workday|servicenow|crm)\s*consulting|'
    r'consultant,?\s*[-–(]?\s*(?:\w+\s+)?(?:security|cyber\w*|cloud|data|ai|ml|technology|technical|digital|'
    r'analytics|engineering|software|devops|infrastructure|migration|sap|salesforce|erp|crm|workday|servicenow)|'
    r'scrum|agile\s*(?:coach|lead|delivery|master|practitioner|program|project)|'
    r'full[-\s]*stack|front[-\s]*end|back[-\s]*end|r&d|'
    # Data / analytics / BI
    r'data\s*(?:scientist|scientists|science|engineer\w*|analyst|analysts|analytics|analysis|architect\w*|'
    r'steward\w*|governance|quality|modeler|modell?ing|specialist|coordinator|manager|management|operations|'
    r'ops|lead|developer|programmer|strategist|strategy|consultant|administrator|admin|curator|annotat\w*|'
    r'label\w*|integration|migration|pipeline|warehous\w*|infrastructure|visualization|visualisation|'
    r'reporting|insights|intern|associate|technician|processor|processing|literacy|evangelist|journalist|'
    r'storyteller|wrangler|apprentice|solutions?|halls?|product\s*(?:manager|owner)|'
    r'platform\s*(?:engineer|architect|developer|manager|lead)|'
    r'protection\s*(?:engineer|officer)|privacy\s*engineer|center|centers|centre|centres|'
    r'(?:&|and)\s*(?:ai|analytics|technology|insights|reporting|ml|machine\s*learning|analysis))|'
    r'big\s*data|master\s*data|'
    r'business\s*(?:analyst|analysts|analysis|intelligence|systems?|technology|tech|applications?|data|analytics|'
    r'process\s*(?:analyst|engineer|automation|improvement\s*analyst)|'
    r'transformation\s*(?:analyst|engineer|architect|consultant)|architecture|architect|insights?|'
    r'systems?\s*analyst)|'
    r'bi\s*(?:developer|analyst|engineer|architect|specialist|consultant|lead|manager|director|intern|associate|'
    r'reporting)|power\s*bi|business\s*intelligence|'
    r'reporting\s*(?:analyst|developer|specialist|engineer|lead|manager|associate|intern|automation|solutions?)|'
    r'report\s*(?:writer|developer|analyst)|dashboard\w*|'
    r'analytics\s*(?:engineer|manager|lead|leader|analyst|consultant|developer|specialist|architect|director|'
    r'scientist|associate|intern|translator|coordinator|partner|program|product|platform|team|head|owner|'
    r'strategist|practitioner|professional|expert|advisor|evangelist|trainer|instructor)|'
    r'(?:data|marketing|product|customer|sales|people|hr|workforce|clinical|healthcare|health|financial|finance|'
    r'risk|fraud|supply\s*chain|operations|business|web|digital|growth|revenue|advanced|predictive|'
    r'prescriptive|real[-\s]*time|sports|audience|media|retail|commerce|e-?commerce|pricing|credit|insurance|'
    r'actuarial|research|experimentation|measurement|attribution|reporting)\s*analytics|'
    r'insights?\s*(?:analyst|manager|lead|director|specialist|associate|engineer|scientist|consultant|partner|'
    r'intern|head|developer)|'
    r'(?:customer|consumer|market|product|user|people|audience|business|marketing|sales|clinical|health)\s*insights?|'
    r'etl|elt|sql|nosql|database|databases|data\s*warehous\w*|lakehouse|'
    r'(?:tableau|power\s*bi|looker|qlik|snowflake|databricks|sql|etl|sas|informatica|talend|ssis|ssrs|'
    r'hadoop|spark|kafka|airflow|dbt|alteryx|microstrategy|cognos)\s*'
    r'(?:developer|analyst|specialist|engineer|administrator|admin|consultant|architect|lead|expert|programmer)|'
    r'statistic\w*|biostat\w*|econometric\w*|epidemiolog\w*|bioinformatic\w*|cheminformatic\w*|'
    r'computational\s*\w+|informatics|informaticist|'
    r'quant\w*\s*(?:analyst|researcher|developer|trader|strategist|engineer|modeler|scientist|risk|finance|'
    r'analytics|research|investment|portfolio|trading|analysis|modeling|modelling|strategy|strategies)|quants?|'
    r'algorithmic\s*trad\w*|algo\s*trad\w*|'
    r'trading\s*(?:systems?|technology|platform|infrastructure|engineer|developer|software)|'
    r'model\s*(?:risk|validation|governance|developer|development|engineer|engineering|ops|operations|'
    r'deployment|training|evaluation)|modeler|'
    r'modell?ing\s*(?:analyst|engineer|scientist|specialist|lead|manager|and\s*simulation|&\s*simulation)|'
    r'simulation\s*(?:engineer|scientist|analyst|developer|modeler)|'
    r'forecast\w*\s*(?:analyst|scientist|engineer|modeler|specialist)|'
    r'operations\s*research|decision\s*scien\w*|'
    r'geospatial|gis|remote\s*sensing|photogrammetry|lidar|cartograph\w*|'
    # AI / ML
    r'(?:machine|deep|reinforcement|statistical)\s*learning|artificial\s*intelligence|'
    r'(?:ai|ml|llm|llms|nlp|genai|gen\s*ai|generative\s*ai|computer\s*vision|cv|speech|conversational\s*ai|'
    r'robotics|autonomy|perception|foundation\s*model|large\s*language\s*model)\s*'
    r'(?:engineer\w*|scientist|scientists|researcher|research|architect|developer|specialist|lead|leader|manager|'
    r'director|head|analyst|consultant|platform|infrastructure|product\s*manager|program\s*manager|ops|'
    r'operations|trainer|tutor|intern|fellow|evangelist|strategist|strategy|risk|transformation|delivery|'
    r'adoption|enablement|agents?|agentic\s*\w+|'
    r'solutions?\s*(?:engineer|architect|consultant)|'
    r'annotat\w*|label\w*|data\s*\w+|quality|safety|alignment|red\s*team\w*|evaluat\w*|ethics|ethicist|'
    r'governance|policy\s*(?:researcher|analyst)|technical\s*\w+|software\s*\w+|systems?\s*\w+|model\w*|'
    r'training|deployment|integration|implementation|practitioner|expert|professional|advocate|educator|'
    r'instructor|curriculum|content\s*(?:writer|developer|evaluator|specialist|reviewer)|prompt\s*\w+|'
    r'linguist|writer|editor|coach|associate|coordinator|team\s*lead|lab|labs|applied|solutions?|'
    r'product\s*(?:owner|lead|manager))|'
    r'search\s*(?:engineer|scientist|relevance|quality\s*(?:engineer|analyst|rater))|'
    r'ranking\s*(?:engineer|scientist)|recommend\w*\s*(?:engineer|scientist|systems?)|'
    r'prompt\s*(?:engineer\w*|designer|specialist|writer)|'
    r'(?:scientist|researcher|research|fellow),?\s*[-–(]?\s*(?:\w+[\s\-–]+)?'
    r'(?:ai|ml|nlp|cv|genai|llms?|deep\s*learning|machine\s*learning|data|analytics|computer\s*vision|'
    r'statistics|modeling|simulation|bioinformatics|perception|robotics|autonomy)|'
    r'(?:data|research|applied|computer|decision|ml|ai|machine\s*learning|nlp|vision|imaging|marketing|product|'
    r'pricing|growth|fraud|risk|forecasting|optimization|operations|behavioral|customer|analytics|quantitative|'
    r'quant|statistical|experimentation|measurement|inference|causal|economics?|search|ranking|recommendation|'
    r'personalization|ads?|advertising|member\s*of\s*technical\s*staff|mts|bio-?informatics?|'
    r'computational\s*\w+|simulation|speech|language|perception|robotics|autonomy|algorithm\w*|controls?|motor|'
    r'physics|systems?|signal|audio|video|image|graphics|rendering|'
    r'software|staff\s*research|principal\s*research|senior\s*research|lead\s*research|people)\s*scientists?|'
    r'(?:ai|ml|machine\s*learning|nlp|llm|research|ux|user|applied|quantitative|quant|security|vulnerability|'
    r'threat|market|data|analytics|design|hci|algorithm\w*|systems?|robotics|vision|speech|'
    r'computer\s*science|software)\s*researchers?|'
    r'research\s*(?:scientist|scientists|engineer|engineers)|applied\s*scientists?|computer\s*scientists?|'
    r'(?:applied|research|computer|data|decision)\s*scien\w*|'
    r'imaging\s*(?:scientist|engineer|analyst|informatics|data|software|algorithm\w*)|'
    r'training\s*data|annotat\w*|'
    # Product / program / project / design
    r'product\s*(?:manager|managers|management|owner|owners|design\w*|analyst|analytics|engineer|lead|director|'
    r'head|operations\s*(?:engineer|analyst)|security|data)|'
    r'program\s*(?:manager|managers|management|lead)|manager,?\s*programs?|'
    r'(?:technical|tech|it|software|engineering|data|implementation|integration|infrastructure|cloud|security|'
    r'cybersecurity|network|systems?|erp|crm|sap|salesforce|workday|servicenow|digital|agile|scrum|devops|'
    r'ai|ml|analytics|bi|technology)\s*(?:project|program|product)\s*(?:manager|lead|coordinator|management)|'
    r'tpm|pm-?t|pmt-es|pmo\s*(?:analyst|lead)|products?\s*(?:manager|managers|management|owner|owners)|'
    r'v&v|hil|verification\s*(?:&|and)\s*validation|quantum\s*computing|'
    r'technical\s*development|technical\s*deployment|security\s*tech\w*|'
    r'(?:design|data|software|information|ai|ml|cloud|digital|creative|learning|solutions?|systems?|security|'
    r'devops|platform|web|ux|chief|enterprise|principal\s*software|senior\s*software)\s*technologists?|'
    r'technologists?,?\s*[-–(]?\s*(?:it|software|data|digital|ai)|'
    r'(?:ux|ui|interaction|experience|service|web|visual|game|level|product|conversation(?:al)?|voice|'
    r'ai|motion\s*ui|content)\s*design\w*|'
    r'design\s*(?:systems?|technologist|engineer|manager|lead|director|researcher|research|ops|operations|strategist)|'
    r'(?:director|vp|vice\s*president|head|manager|lead),?\s*(?:of\s*)?products?'
    r'(?!\s*(?:marketing|finance|sales|support|operations|ops|compliance|legal|safety|quality|supply|sourcing|'
    r'content|communications?|stewardship|surveillance|labeling))|'
    r'creative\s*technologist|'
    r'ux|ui|user\s*(?:experience|interface|research\w*)|usability|'
    r'design\s*verification|'
    # Security
    r'cyber\w*\s*(?:engineer|analyst|architect|specialist|consultant|researcher|operations|intern|associate|lead|'
    r'manager|director|defense|threat|incident|risk|security\s*\w+)|'
    r'security\s*(?:engineer|engineers|analyst|analysts|architect|researcher|consultant|operations|administrator|'
    r'specialist|developer|intern|associate|program\s*manager|product\s*manager|assurance|compliance|awareness|'
    r'automation|tooling|platform|software|data|analytics|response|testing|tester|clearance)|'
    r'infosec|information\s*security|isso|issm|grc|penetration|pentest\w*|pen\s*test\w*|'
    r'red\s*team|blue\s*team|purple\s*team|threat\s*(?:intel\w*|hunt\w*|research\w*|detection|analyst)|'
    r'vulnerability|malware|incident\s*(?:response|responder|manager|management|commander)|'
    r'digital\s*forensic\w*|soc|siem|identity\s*(?:and|&)?\s*access\s*management|identity\s*(?:and|&)\s*access|iam|'
    r'appsec|application\s*security|'
    r'cloud\s*security|network\s*security|product\s*security|offensive\s*security|detection\s*engineer\w*|'
    r'endpoint\s*(?:engineer|security|management|administrator)|'
    # QA / test
    r'qa|quality\s*(?:assurance|engineer\w*|automation)|software\s*quality|'
    r'test\s*(?:engineer|engineers|automation|analyst|lead|architect|manager|developer)|sdet|tester|'
    # IT / infrastructure / support / admin
    r'information\s*(?:technology|systems?|architect\w*|management\s*(?:system|technology|analyst))|'
    r'it\s*(?:manager|director|specialist|support|admin\w*|operations|ops|analyst|technician|generalist|'
    r'coordinator|helpdesk|lead|associate|intern|engineer|project|program|service|services|business|audit\w*|'
    r'compliance|risk|governance|asset|procurement|security|systems?|infrastructure|architect|consultant|'
    r'field|desktop|applications?|supervisor|professional|trainee|apprentice|officer|head|portfolio|pmo|'
    r'vendor|sourcing|transformation|strateg\w*|delivery|change|release|incident|problem|service\s*management|'
    r'sox|staff|team|installation|installer|internal\s*audit\w*)|'
    r'it\s*(?:\w+\s+){0,3}(?:tech|techs|technicians?|support)|'
    r'help\s*desk|helpdesk|service\s*desk|desktop\s*support|'
    r'(?:technical|tech)\s*(?:\w+\s+)?support|'
    r'(?:it|application|app|production|systems?|network|infrastructure|cloud|developer|'
    r'platform|computer|hardware|technology|end[-\s]*user|desktop|field\s*technical|sql|database|linux|'
    r'windows|server|storage)\s*support|'
    r'(?:systems?|database|network|salesforce|workday|servicenow|sharepoint|linux|windows|server|cloud|aws|'
    r'azure|gcp|security|platform|application|applications|crm|erp|sap|oracle|hris|jira|atlassian|okta|'
    r'm365|microsoft\s*365|office\s*365|exchange|unix|storage|backup|vmware|infrastructure|data(?:base)?|'
    r'unified\s*communications|telecom\w*|voip|web|email|messaging|directory|active\s*directory|identity|'
    r'endpoint|desktop|kubernetes|devops|cyber\w*|epic|cerner|lims|plm|cad|teamcenter|windchill|solidworks|'
    r'catia|autocad|mes|scada|wms|tms|netsuite|dynamics|'
    r'hubspot|marketo|zendesk|tableau|snowflake|databricks|hadoop|splunk|sql|nosql|mongodb|postgres\w*|mysql|'
    r'redis|elastic\w*|lan|wan|firewall|voice|collaboration\s*tools?)\s*admin\w*|'
    r'(?:network|systems?|it|data\s*cent(?:er|re)s?|datacenter|computer|desktop|help\s*desk|helpdesk|'
    r'service\s*desk|support|infrastructure|cloud|telecom\w*|telecommunications|noc|server|storage|'
    r'application|applications|gis|av|audio\s*visual|audio-visual|broadcast\s*it|it\s*field|'
    r'field\s*service\s*it|lab\s*it|cabling|network\s*cabling|structured\s*cabling)\s*technicians?|'
    r'end[-\s]*user\s*(?:\w+\s+){0,3}technicians?|(?:av|vtc|av/vtc)\)?\s*technicians?|'
    r'technicians?,?\s*[-–(]?\s*(?:it|information\s*technology|desktop|network|data\s*cent\w*|help\s*desk|'
    r'support)|'
    # Technology / tool word + role noun. Nouns like technician / operator /
    # coordinator are deliberately limited to IT & software stack words;
    # hardware-adjacent words only pair with engineering/analysis nouns.
    r'(?:it|data|analytics|bi|business\s*intelligence|salesforce|crm|erp|sap|workday|servicenow|hris|cloud|aws|'
    r'azure|gcp|cyber\w*|network|networking|systems?|database|application|applications|technical|'
    r'technology|tech|digital\s*(?:product|platform|technology|transformation)|reporting|integration|'
    r'implementation|computer|web|gis|geospatial|configuration|release|devops|infrastructure|platform|'
    r'ai|ml|machine\s*learning|nlp|llm|genai|data\s*science|analytics|statistical|sas|sql|etl|tableau|'
    r'power\s*bi|looker|snowflake|databricks|python|java|linux|windows|server|storage|backup|virtualization|'
    r'vmware|citrix|m365|microsoft\s*365|office\s*365|sharepoint|okta|jira|atlassian|epic|cerner|lims|plm|'
    r'mes|netsuite|dynamics|pega|appian|uipath|rpa|mulesoft|boomi|informatica|'
    r'talend|hadoop|spark|kafka|airflow|dbt|splunk|elastic\w*|mongodb|postgres\w*|mysql|redis|'
    r'oracle\s*(?:ebs|fusion|cloud|database|db|apps|applications|erp|hcm)|low[-\s]*code|no[-\s]*code|'
    r'blockchain|web3|solidity|smart\s*contract|quantum|hpc|gpu|cuda|kernel|compiler|operating\s*systems?|'
    r'distributed\s*systems?|frontend|backend|javascript|typescript|golang|rust|scala|kotlin|swift|react|'
    r'angular|vue|node\.?js|solutions?|technical\s*solutions?|pre-?sales|presales|'
    r'(?:software|application|app|web|game|mobile|platform|firmware|embedded|backend|frontend|full[-\s]*stack|'
    r'product)\s*(?:development|dev))\s*'
    r'(?:specialist|specialists|consultant|consultants|technician|coordinator|intern|associate|trainee|'
    r'apprentice|fellow|expert|professional|practitioner|evangelist|advocate|champion|trainer|instructor|'
    r'educator|writer|author|lead|leader|manager|director|head|supervisor|operator|owner|'
    r'product\s*manager|program\s*manager|project\s*manager|tester|designer|technologist|dev|'
    r'engineer\w*|developer|developers|architect\w*|analyst|analysts|administrator|admin|researcher|'
    r'scientist|programmer|team\s*lead|integrator)|'
    r'(?:embedded|firmware|hardware|fpga|asic|rtl|dsp|signal\s*processing|controls|robotics|perception|'
    r'autonomy|automation|wireless|5g|iot|edge|graphics|rendering|game|gameplay|tools|build|api|sdk|mobile|'
    r'ios|android|streaming|video\s*(?:streaming|platform|codec|encoding)|deployment\s*(?=engineer)|observability|'
    r'monitoring|reliability|capacity|performance|tooling|middleware|mainframe|cobol|'
    r'developer\s*(?:experience|productivity|tools|platform|relations|advocacy|education|marketing)|'
    r'plc|scada|hmi)\s*'
    r'(?:engineer\w*|developer|developers|architect\w*|analyst|analysts|scientist|programmer|designer|tester|'
    r'researcher|specialist|consultant|lead|manager|director|head|intern)|'
    r'computer\s*(?:vision|science|scientist|operator|technician|engineer|programmer|systems?|support|'
    r'specialist|analyst|graphics|architecture|security|networking)|'
    r'solutions?\s*(?:engineer|engineers|architect|architects|consultant|consultants|developer|specialist|analyst|'
    r'engineering|architecture)|'
    r'implementation\s*(?:engineer|specialist|consultant|analyst|architect|lead|manager)|'
    r'integration\s*(?:engineer|specialist|consultant|analyst|architect|lead|manager|developer)|'
    r'forward\s*deployed|customer\s*engineer\w*|field\s*(?:engineer|application\s*engineer|cto)|'
    r'pre-?sales\s*(?:engineer|consultant|architect|solutions?)|sales\s*engineer\w*|'
    r'technical\s*(?:writer|lead|director|program|project|product|account|artist|consultant|architect|analyst|'
    r'specialist|support|trainer|training|instructor|manager|staff|solutions?|evangelist|advisor|delivery|'
    r'success|operations|curriculum|documentation|pm|owner|producer|animator|fellow|associate|coordinator|'
    r'services|service|expert|professional|engineer\w*|lead\w*|team\s*lead|supervisor|'
    r'business\s*analyst|systems\s*analyst|designer|strategist|marketing\s*engineer|sales\s*engineer|'
    r'presales|pre-sales|enablement)|technical(?=\s*[)\]]|\s*$)|'
    r'project\s*manager,?\s*[-–(]?\s*(?:it|technology|software|engineering|data|digital|technical|infrastructure|'
    r'cloud|security|systems|erp|crm|sap|salesforce|workday|servicenow|implementation|integration)|'
    r'network\s*services|'
    r'tech\s*(?:lead|leads|ops|operations|support|writer|specialist|analyst|consultant|manager|director|'
    r'program\s*manager|project\s*manager|product\s*manager|evangelist|advocate)|'
    r'technology\s*(?:lead|leads|manager|director|analyst|consultant|specialist|architect|officer|associate|'
    r'intern|engineer|advisor|risk|audit\w*|operations|ops|services|strategist|strategy|program\s*manager|'
    r'project\s*manager|product\s*manager|evangelist|head|team|transformation)|'
    r'member\s*of\s*technical\s*staff|mts|technical\s*staff|'
    r'chief\s*(?:technology|information|data|analytics|ai|digital|architect|product|security|'
    r'information\s*security|scientist|technical|innovation)|cto|cio|cdao|ciso|'
    r'head\s*of\s*(?:engineering|data|analytics|technology|it|infrastructure|platform|security|ai|ml|'
    r'machine\s*learning|product|design|software|devops|cloud|research|architecture|bi|'
    r'business\s*intelligence|data\s*science|quality\s*engineering|qa|technical\s*\w+|information\s*\w+|'
    r'\w+\s+engineering|\w+\s+(?:data|analytics|technology|ai|ml))|'
    # Leadership roles whose scope is a technical function:
    # "Director, Marketing Analytics", "Manager, Data", "VP, Technology"
    r'(?:^|[,\-–—/|(]\s*)'
    r'(?:(?:senior|sr\.?|associate|assistant|group|executive|principal|staff|global|regional|deputy|interim|'
    r'acting|managing|general|area|divisional|corporate)\s+)*'
    r'(?:head|director|vp|vice\s*president|manager|lead|chief|president|svp|evp|avp|gm|general\s*manager|'
    r'supervisor|principal|fellow)'
    r'(?:\s*(?:of|,|-|–|—|/)\s*|\s+)(?:\w+[\s&/]+){0,3}?'
    r'(?:analytics|data|insights|business\s*intelligence|bi|data\s*science|machine\s*learning|ai|ml|'
    r'engineering|technology|technologies|it|software|infrastructure|platform|platforms|security|'
    r'information\s*security|cybersecurity|cyber\s*security|devops|cloud|architecture|systems|applications?|'
    r'digital\s*(?:product|platform|technology)|design|ux|product|informatics|statistics|biostatistics|'
    r'data\s*engineering|data\s*platform|data\s*products?|technical\s*\w+|computing|quantitative\s*\w+|'
    r'quant|research\s*(?:science|engineering)|algorithms?|automation|integration|dbas?|databases?|'
    r'networks?|networking|telecom\w*|interoperability|identity|observability|reliability|sre|'
    r'developer\s*(?:experience|relations|tools|platform|productivity)|'
    r'(?:software|data|platform|ml|ai|analytics|systems?|network|cloud|security|infrastructure|quality|test)'
    r'\s*(?:engineering|development|science|architecture|operations|ops))'
    r'(?=\s*(?:\([^)]*\))?\s*(?:[-–—,/|]|$))|'
    # Business-function technology teams (HR Tech, Finance Systems, Data &
    # Technology) and data-center work are IT roles.
    r'(?:hr|people|finance|financial|accounting|marketing|sales|revenue|gtm|legal|recruiting|business|payroll|'
    r'tax|clinical|health(?:care)?|supply\s*chain|procurement|'
    r'logistics|commerce|ecommerce|e-commerce|retail|store|customer|client|enterprise|corporate|digital|'
    r'learning|education|student|academic|library|benefits|compensation|treasury|risk|fraud|credit|insurance|'
    r'claims|underwriting|lending|mortgage|banking|wealth|investment|trading|pricing|operations|'
    r'manufacturing|production|regulatory|compliance|audit|grants?|events?|content|media|advertising|ads?|'
    r'campaign|community|partner|channel|growth|product|program|project|portfolio|talent|workforce|employee|'
    r'workplace|facilities|real\s*estate|dealer|fleet|government|public\s*sector|policy|court|transit|'
    r'transportation|hotel|hospitality|restaurant|food|grocery|research|science|scientific|construction|'
    r'energy|utility|utilities|telecom\w*|broadcast|news|publishing|gaming|sports|entertainment|music|film|'
    r'studio|creative|brand|design|travel|nonprofit|church|school|university|college|campus|museum)'
    r'\s*(?:technology|tech|systems?|tooling|platform\s*(?:engineer|developer|architect)|engineering|'
    r'informatics|analytics|data\s*(?:analyst|engineer|scientist|science|analytics|architect|manager|lead|'
    r'specialist|coordinator|steward|governance|quality|operations|ops|platform\s*engineer))|'
    r'data\s*cent(?:er|re)s?|datacenter|'
    r'hris|hrit|hr\s*(?:systems?|technology|tech|analytics|data|information\s*systems?)|'
    r'people\s*(?:analytics|systems|data|technology|tech|insights|scientist|science)|'
    r'(?:salesforce|sap|oracle|workday|servicenow|netsuite|hris|erp|crm|dynamics|peoplesoft|epic|cerner|'
    r'sharepoint|m365|microsoft\s*365|office\s*365|okta|jira|atlassian|mulesoft|boomi|sitecore|aem|'
    r'guidewire|duck\s*creek|pega|appian|uipath|automation\s*anywhere|blue\s*prism|rpa|anaplan|coupa|ariba|'
    r'blackline|kronos|ukg|ceridian|dayforce|successfactors|veeva|iqvia|medidata|lims|plm|mes|scada|wms|tms)\s*'
    r'(?:developer|engineer|analyst|architect|admin\w*|consultant|specialist|lead|programmer|technical|expert|'
    r'configurator|implementation|integration|functional|technical\s*\w+|solution\w*|systems?|platform|'
    r'product\s*owner|business\s*analyst|data\s*\w+|practice|program\s*manager|project\s*manager|'
    r'manager|team\s*lead|support)|'
    r'erp|crm\s*(?:developer|admin\w*|analyst|engineer|architect|consultant|technical|systems?|data\s*\w+|'
    r'platform|product\s*manager|implementation|integration)|'
    r'web\s*(?:developer|designer|engineer|master)|mobile\s*(?:developer|engineer)|'
    r'ios\s*(?:developer|engineer)|android\s*(?:developer|engineer)|'
    r'game\s*(?:developer|programmer|engineer|designer|producer)|technical\s*(?:producer|artist|animator)|'
    r'embedded|firmware|fpga|asic|rtl|'
    r'(?:cloud|aws|azure|gcp|kubernetes|k8s|docker|terraform|linux|unix|windows|vmware|citrix|cisco|storage|'
    r'backup|virtualization|network|networking|infrastructure|datacenter|data\s*center|platform|release|build|'
    r'configuration|integration|middleware|mainframe|cobol|erp|server|observability|monitoring|reliability|'
    r'capacity|performance|tooling|api|sdk)\s*'
    r'(?:engineer\w*|architect\w*|admin\w*|specialist|analyst|consultant|technician|developer|lead|manager|'
    r'operations|ops|support|planner|planning|advocate|evangelist|intern|associate)|'
    r'(?:capacity|network|infrastructure|data\s*cent\w*|it|technology|release|sprint|test)\s*plann\w*|'
    r'cryptograph\w*|crypto\s*(?:engineer|developer)|blockchain\s*(?:engineer|developer|architect)|'
    r'web3\s*(?:engineer|developer)|smart\s*contract|'
    r'site\s*reliability|reliability\s*engineer\w*|'
    r'developer\s*(?:relations|advocate|advocacy|evangelist|experience|education|marketing|community|'
    r'productivity|tools|platform)|devrel|'
    r'(?:ai|llm|ml|model|data)\s*(?:trainer|tutor|data\s*trainer)|coding\s*(?:instructor|tutor|teacher|coach|'
    r'bootcamp)|technical\s*train\w*|engineering\s*coach|'
    r'(?:unified\s*communications|telecom\w*|voip|collaboration)\s*(?:engineer|administrator|admin|specialist|'
    r'analyst|technician)|'
    r'emerging\s*tech\w*|deployment\s*strategist|'
    r'clinical\s*(?:data|analyst|informatics|informaticist|programm\w*|systems?|application|applications|it|'
    r'technology|analytics|reporting|statistic\w*|statistician|database|sas)|'
    r'(?:data|model|content|ai|ml|llm|image|video|text|speech|audio)\s*(?:annotator|annotation|labeler|'
    r'labeling|labelling|rater|raters|evaluator|reviewer)|'
    r'server\s*(?:engineer|admin\w*|developer|architect|support|specialist|technician|operations|infrastructure)|'
    r'analytics\s*translator|localization\s*engineer\w*|'
    r'instrumentation\s*(?:and|&)\s*controls?|controls\s*(?:engineer|systems?|software)|'
    r'automation\s*(?:engineer|developer|architect|specialist|analyst|lead|tester|test)|'
    r'computer\s*(?:science|engineering)|electrical\s*(?:and|&)\s*computer'
    r')\b',
    re.IGNORECASE,
)


# The head of a title is the role; what follows the first separator is the
# team or product ("AI Solutions Engineer, Talent Acquisition", "Senior FPGA
# & Firmware Engineer - SDR"). Hard patterns are checked on the head first so
# a department word in the tail cannot sink a technical head role.
_TITLE_HEAD_SPLIT = re.compile(r'\s*(?:,|\||:|\(|\s[-–—]\s|[-–—]\s|[–—]|\s/(?=\S))')


_BARE_ANALYST = re.compile(r'\banalysts?\b', re.IGNORECASE)


def _title_head(title: str) -> str:
    return _TITLE_HEAD_SPLIT.split(title, 1)[0]


def is_non_it_title(title) -> bool:
    """True when the title should be dropped as a non-technical role."""
    if not isinstance(title, str) or not title.strip():
        return False
    title = title.replace("_", " ")
    head = _title_head(title)
    # 1. An unambiguous non-technical role noun at the head wins outright.
    if HARD_NON_IT_TITLE_PATTERNS.search(head):
        return True
    # 2. A non-technical function's programme management at the head drops
    #    unless the title carries a strong data signal ("Supply Chain Program
    #    Manager" drops; "Finance Program Manager, Analytics & Reporting" stays).
    if CONTEXT_NON_IT_TITLE_PATTERNS.search(head):
        return not STRONG_DATA_TITLE_PATTERNS.search(title)
    # 3. A technical role at the head cannot be sunk by a department word in
    #    the tail. A bare "Analyst" head says nothing about the function, so
    #    the tail decides ("Analyst, Finance" drops, "Analyst, Data Platform"
    #    stays).
    if head != title and IT_TITLE_PATTERNS.search(_BARE_ANALYST.sub("", head)):
        return False
    # 4. Role nouns that are unambiguous anywhere ("..., Recruiter - Data
    #    Science", "... Attorney, Vice President").
    if ANYWHERE_NON_IT_TITLE_PATTERNS.search(title):
        return True
    # 5. A strong data / engineering signal anywhere keeps the title.
    if STRONG_DATA_TITLE_PATTERNS.search(title):
        return False
    # 6. Hard role nouns anywhere, then soft signal without a technical role.
    if HARD_NON_IT_TITLE_PATTERNS.search(title) or CONTEXT_NON_IT_TITLE_PATTERNS.search(title):
        return True
    return bool(NON_IT_TITLE_PATTERNS.search(title) and not IT_TITLE_PATTERNS.search(title))


# Explicit non-US countries / cities / regions. Substring match on the
# lower-cased location AFTER the US signals (state codes, state names, US
# cities) have had their say, so "Milwaukee" (contains "uk") and "Indiana"
# (contains "india") are already accepted by then.
NON_US_SIGNALS = [
    'international', 'united kingdom', 'england', 'scotland', 'wales', 'london', 'manchester',
    'edinburgh', 'glasgow', 'leeds', 'belfast',
    'canada', 'canadian', 'toronto', 'vancouver', 'montreal', 'ottawa', 'calgary', 'waterloo', 'ontario', 'quebec',
    'bangalore', 'bengaluru', 'hyderabad', 'mumbai', 'pune', 'noida', 'gurgaon', 'gurugram', 'chennai', 'delhi',
    'germany', 'berlin', 'munich', 'hamburg', 'frankfurt', 'cologne', 'stuttgart',
    'france', 'paris', 'japan', 'tokyo', 'osaka',
    'singapore', 'taiwan', 'taipei',
    'australia', 'sydney', 'melbourne', 'brisbane', 'perth',
    'new zealand', 'auckland', 'wellington',
    'ireland', 'dublin', 'netherlands', 'amsterdam', 'rotterdam', 'utrecht',
    'belgium', 'brussels', 'antwerp', 'luxembourg',
    'spain', 'madrid', 'barcelona',
    'italy', 'milan', 'brazil', 'sao paulo', 'são paulo',
    'mexico', 'mexico city', 'guadalajara', 'monterrey',
    'korea', 'seoul',
    'china', 'beijing', 'shanghai', 'shenzhen', 'hangzhou',
    'hong kong', 'israel', 'tel aviv', 'herzliya',
    'switzerland', 'zurich', 'zürich', 'basel', 'geneva', 'lausanne',
    'sweden', 'stockholm', 'gothenburg',
    'norway', 'oslo', 'denmark', 'copenhagen', 'aarhus', 'finland', 'helsinki',
    'austria', 'vienna', 'greece', 'athens', 'hungary', 'budapest', 'turkey', 'istanbul', 'ankara',
    'poland', 'warsaw', 'krakow', 'kraków', 'wroclaw', 'wrocław', 'gdansk',
    'czech', 'prague', 'slovakia', 'bratislava', 'slovenia', 'croatia', 'zagreb', 'serbia', 'belgrade',
    'bulgaria', 'sofia', 'romania', 'bucharest', 'cluj', 'ukraine', 'kyiv', 'kiev',
    'lithuania', 'vilnius', 'latvia', 'estonia', 'tallinn',
    'portugal', 'lisbon', 'philippines', 'manila', 'cebu',
    'vietnam', 'ho chi minh', 'hanoi',
    'thailand', 'bangkok',
    'malaysia', 'kuala lumpur', 'indonesia', 'jakarta',
    'pakistan', 'karachi', 'lahore', 'bangladesh', 'dhaka', 'sri lanka', 'colombo',
    'uae', 'dubai', 'abu dhabi', 'emirates',
    'saudi', 'riyadh', 'qatar', 'doha', 'bahrain', 'kuwait', 'amman', 'lebanon', 'beirut',
    'egypt', 'cairo', 'morocco', 'casablanca', 'ghana', 'accra',
    'kenya', 'nairobi',
    'south africa', 'cape town', 'johannesburg',
    'nigeria', 'lagos',
    'argentina', 'buenos aires',
    'colombia', 'bogota', 'bogotá', 'medellin', 'medellín',
    'chile', 'santiago', 'uruguay', 'montevideo', 'ecuador', 'costa rica', 'san josé, costa rica',
    'emea', 'apac', 'latam', 'dach', 'benelux', 'nordics',
    'djibouti', 'iceland', 'malta', 'cyprus',
    'british columbia', 'nova scotia', 'saskatchewan', 'manitoba',
]

# Short names that are substrings of US places ("Milwaukee", "Indian Wells",
# "Brigantine", "Romania") — matched as whole words only.
_NON_US_WORDS = ['uk', 'u.k.', 'india', 'oman', 'peru', 'jordan', 'anz', 'riga', 'porto', 'cork', 'lyon', 'rome']

# Every non-US name is matched as a whole word (never a substring): "New
# England", "St. Tammany Parish" (paris), "Londonderry" and "Dachshund Lane"
# (dach) were being dropped as foreign. Multi-word names keep their spaces.
_NON_US_RE = re.compile(
    r'(?<![\w])(?:' + '|'.join(re.escape(s) for s in sorted(NON_US_SIGNALS + _NON_US_WORDS, key=len, reverse=True)) + r')(?![\w])',
    re.IGNORECASE,
)

# Positive US signals as whole words. "US & Canada", "Remote (US or Canada)",
# "US/Canada" and "New England" are US-eligible postings and used to fall
# through to the Canada / England checks.
_US_WORDS = re.compile(
    r'(?<![\w])(?:us|u\.s\.a?\.?|u\.s|usa|united states|new england|puerto rico|'
    r'washington,?\s*d\.?c\.?|nyc|sf bay area)(?![\w])',
    re.IGNORECASE,
)

# Upper-case ISO alpha-2/alpha-3 codes that are not US state codes and not
# compass points / English words seen in street addresses (SE, NO, IT, AT...).
# A digit on either side is not a code boundary: "4TH FLOOR" is not Thailand.
_NON_US_ISO_CODES = re.compile(
    r'(?<![A-Za-z0-9])(?:'
    r'IE|AU|GB|FR|NL|ES|JP|SG|BR|MX|CH|PL|PT|NZ|ZA|AE|KR|CN|HK|TW|BE|DK|FI|CZ|RO|EG|NG|KE|PH|VN|TH|UA|'
    r'DEU|GBR|FRA|CAN|CAD|AUS|IND|NLD|IRL|ESP|ITA|CHE|SWE|NOR|DNK|FIN|POL|BEL|AUT|PRT|CZE|ROU|HUN|GRC|TUR|ISR|ARE|SGP|JPN|KOR|CHN|TWN|HKG|MEX|BRA|ARG|COL|CHL|PER|NZL|ZAF|EGY|NGA|KEN|PHL|VNM|THA|MYS|IDN|PAK|BGD|LKA|UKR'
    r')(?![A-Za-z0-9])'
)

# Max age for jobs — drop anything older than this
MAX_JOB_AGE_DAYS = 90


class ValidatePipeline:
    """Drop items that don't meet minimum data quality requirements."""

    REQUIRED_FIELDS = ["source", "source_job_id", "title", "apply_url"]

    def __init__(self):
        self.dropped = 0
        self.dropped_non_it = 0
        self.dropped_non_us = 0
        self.dropped_old = 0
        self.warnings = 0

    def process_item(self, item, spider):
        # 1. Required fields
        for field in self.REQUIRED_FIELDS:
            value = item.get(field)
            if not value or (isinstance(value, str) and not value.strip()):
                self.dropped += 1
                raise DropItem(
                    f"Missing required field '{field}' in job "
                    f"{item.get('source_job_id', '?')} from {item.get('company_slug', '?')}"
                )

        # 2. Must have description
        if not item.get("description_html") and not item.get("description_text"):
            self.dropped += 1
            raise DropItem(
                f"No description for job {item.get('source_job_id', '?')} "
                f"from {item.get('company_slug', '?')}"
            )

        # 2b. Identity / link columns that cannot be shortened: a truncated
        # apply_url is a dead link and a truncated source_job_id is another
        # job (and in strict sql_mode either sinks the whole company batch).
        for field, limit in MAX_LEN_IDENTITY.items():
            value = item.get(field)
            if isinstance(value, str) and len(value) > limit:
                self.dropped += 1
                raise DropItem(
                    f"{field} too long ({len(value)} > {limit}) for job "
                    f"{item.get('source_job_id', '?')} from {item.get('company_slug', '?')}"
                )

        title = item.get("title", "")

        # 3. Technical jobs only — see is_non_it_title()
        if is_non_it_title(title):
            self.dropped_non_it += 1
            raise DropItem(f"Non-IT job: '{title}' from {item.get('company_slug', '?')}")

        # 4. US-only filter — check location field for non-US indicators
        if not self._is_likely_us(item):
            self.dropped_non_us += 1
            raise DropItem(f"Non-US job: '{title}' location='{item.get('location', '')}' from {item.get('company_slug', '?')}")

        # 5. Age filter — drop jobs older than 90 days
        posted_at = item.get("posted_at")
        if posted_at and self._is_too_old(posted_at):
            self.dropped_old += 1
            raise DropItem(f"Old job (>{MAX_JOB_AGE_DAYS}d): '{title}' posted={posted_at}")

        # Warnings for low quality
        if not item.get("location"):
            self.warnings += 1
        if not item.get("posted_at"):
            self.warnings += 1

        return item

    def _is_likely_us(self, item) -> bool:
        """
        Determine if a job is likely US-based.
        At scrape time we don't have enriched location_country yet,
        so we check raw location text for non-US signals.
        """
        location = (item.get("location") or "").strip()

        # No location = allow through (enrichment will classify later)
        if not location:
            return True

        loc_lower = location.lower()

        # Explicit US indicators — definitely US
        us_signals = [
            "united states", ", us", " us ", "(us)", "usa",
            # US state abbreviations after comma: ", CA", ", NY", etc.
        ]
        for sig in us_signals:
            if sig in loc_lower:
                return True
        if _US_WORDS.search(location):
            return True

        # Check for US state abbreviations (", XX" pattern)
        us_state_abbrevs = {
            'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in',
            'ia','ks','ky','la','me','md','ma','mi','mn','ms','mo','mt','ne','nv',
            'nh','nj','nm','ny','nc','nd','oh','ok','or','pa','ri','sc','sd','tn',
            'tx','ut','vt','va','wa','wv','wi','wy','dc'
        }
        # Match patterns like "San Francisco, CA" or "Austin, TX"
        state_match = re.search(r',\s*([A-Z]{2})\b', location)
        if state_match and state_match.group(1).lower() in us_state_abbrevs:
            return True

        # US state full names
        us_states = [
            'alabama','alaska','arizona','arkansas','california','colorado',
            'connecticut','delaware','florida','georgia','hawaii','idaho',
            'illinois','indiana','iowa','kansas','kentucky','louisiana',
            'maine','maryland','massachusetts','michigan','minnesota',
            'mississippi','missouri','montana','nebraska','nevada',
            'new hampshire','new jersey','new mexico','new york',
            'north carolina','north dakota','ohio','oklahoma','oregon',
            'pennsylvania','rhode island','south carolina','south dakota',
            'tennessee','texas','utah','vermont','virginia','washington',
            'west virginia','wisconsin','wyoming','district of columbia'
        ]
        # Whole words only: "Cambridgeshire, England" is not Cambridge, MA.
        for state in us_states:
            if re.search(r'(?<![\w])' + re.escape(state) + r'(?![\w])', loc_lower):
                return True

        # Major US cities (no state needed)
        us_cities = [
            'san francisco','los angeles','new york','chicago','seattle',
            'austin','boston','denver','atlanta','dallas','houston',
            'san jose','san diego','portland','phoenix','philadelphia',
            'minneapolis','miami','detroit','charlotte','nashville',
            'raleigh','salt lake','las vegas','sacramento','pittsburgh',
            'st. louis','st louis','tampa','orlando','indianapolis',
            'columbus','cleveland','kansas city','milwaukee','richmond',
            'boulder','palo alto','mountain view','sunnyvale','cupertino',
            'redmond','menlo park','santa clara','irvine','plano',
            'arlington','bellevue','ann arbor','durham','chapel hill',
            'cambridge','somerville','fort worth','fort meade',
            'fort bragg','fort liberty','huntsville',
            'west jordan','south jordan',   # Utah; "Jordan" alone is the country
        ]
        for city in us_cities:
            if re.search(r'(?<![\w])' + re.escape(city) + r'(?![\w])', loc_lower):
                return True

        # Canadian province codes after a comma ("Markham, ON", "Vancouver, BC")
        # look exactly like the US "City, ST" pattern but none of these codes
        # is a US state — checked after the US signals so "..., ON, United
        # States" style strings are unaffected.
        if re.search(r',\s*(ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b', location):
            return False

        # ISO country codes ATSs append or prefix ("Dublin, IE", "Sydney, AU",
        # "DEU - Remote - Munich", "CAD - Toronto"). Upper-case only, and only
        # codes that are not also a US state code.
        if _NON_US_ISO_CODES.search(location):
            return False

        # Explicit non-US countries/cities — definitely not US. Checked BEFORE
        # the generic "remote" allowance: "Remote - United Kingdom", "Belgium -
        # Remote", "UAE - Remote" and "Oslo - Remote" were all being kept
        # because the remote branch returned first.
        if _NON_US_RE.search(location):
            return False

        # "Remote" with no country qualifier = allow through (likely US)
        if loc_lower in ('remote', 'remote, us', 'remote - us', 'anywhere'):
            return True
        if re.match(r'^remote\s*[-–/]\s*(us|usa|united states)', loc_lower):
            return True
        # Generic "Remote" without country = allow (most of our sources are US companies)
        if 'remote' in loc_lower:
            return True

        # If we can't determine, allow through (enrichment will handle)
        return True

    def _is_too_old(self, posted_at: str) -> bool:
        """Check if posted_at is older than MAX_JOB_AGE_DAYS."""
        try:
            dt = datetime.strptime(posted_at[:19], "%Y-%m-%d %H:%M:%S")
            cutoff = datetime.now() - timedelta(days=MAX_JOB_AGE_DAYS)
            return dt < cutoff
        except (ValueError, TypeError):
            return False

    def close_spider(self, spider):
        spider.validation_failures = self.dropped + self.dropped_non_it + self.dropped_non_us + self.dropped_old
        spider.validation_warnings = self.warnings
        total_dropped = self.dropped + self.dropped_non_it + self.dropped_non_us + self.dropped_old
        if total_dropped:
            spider.logger.info(
                f"Validation: dropped {total_dropped} items — "
                f"{self.dropped} invalid, {self.dropped_non_it} non-IT, "
                f"{self.dropped_non_us} non-US, {self.dropped_old} too old"
            )
        if self.warnings:
            spider.logger.info(f"Validation: {self.warnings} low-quality warnings")
