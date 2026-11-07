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


# ── Non-IT title patterns (case-insensitive) ──────────
# If a title matches ANY of these, the job is dropped.
NON_IT_TITLE_PATTERNS = re.compile(
    r'\b('
    # Food / hospitality
    r'cook|chef|baker|bartender|barista|waiter|waitress|busser|dishwasher|'
    r'food\s*service|catering|kitchen|sous\s*chef|line\s*cook|prep\s*cook|'
    # Trades / physical labor
    r'electrician|plumber|carpenter|welder|mechanic|hvac|janitor|custodian|'
    r'landscap\w*|roofer|mason|painter(?!\s*(ui|ux|product|visual))|'
    r'forklift|warehouse\s*associate|warehouse\s*worker|'
    r'material\s*handler|machine\s*operator|assembler|floor\s*tech\w*|'
    r'production\s*(operator|associate|technician|worker|process\s*technician)|'
    r'maintenance\s*technician|quality\s*inspector|'
    r'truck\s*driver|cdl\s*driver|delivery\s*driver|'
    # Healthcare / medical  (prefix stems need \w* — a bare stem followed by
    # the group's closing \b can never match "radiologist" / "landscaper")
    r'nurse\b|nursing|rn\b|lpn\b|cna\b|phlebotom\w*|radiolog\w*|'
    r'medical\s*assistant|dental|hygienist|pharmacist|pharmacy\s*tech|'
    r'physician|surgeon|therapist(?!\s*(ui|ux))|paramedic|emt\b|'
    # Legal (non-tech)
    r'paralegal|legal\s*secretary|legal\s*assistant|attorney|lawyer|'
    # Retail / sales floor
    r'retail\s*sales\s*associate|cashier|store\s*clerk|'
    r'merchandiser|stock\s*clerk|stocker|'
    # Military / defense operations (non-IT)
    r'humint|sigint|geoint|imagery\s*analyst|biometric|fingerprint|'
    r'intelligence\s*(instructor|trainer|collector)|'
    r'combat|infantry|artillery|munitions|armament|ordnance|'
    r'force\s*protection|security\s*guard|armed\s*guard|'
    # Construction
    r'construction\s*(worker|laborer|foreman|superintendent)|'
    r'heavy\s*equipment\s*operator|crane\s*operator|'
    r'surveyor(?!\s*(software|data))|'
    # Transportation / logistics (non-tech)
    r'diesel\s*mechanic|fleet\s*mechanic|auto\s*mechanic|'
    r'dispatcher(?!\s*(software|system))|'
    # Administrative (generic non-tech)
    r'receptionist|file\s*clerk|mail\s*clerk|'
    # Education (non-tech)
    r'teacher(?!\s*(tech|software|engineering))|'
    r'substitute\s*teacher|tutor(?!\s*(ai|software|data))|'
    # Cleaning / maintenance
    r'housekeeper|housekeeping|maid|cleaning|'
    # Business functions at tech companies — not IT roles. On a 6.7k-job
    # sample 24% of kept titles were sales / recruiting / finance / HR /
    # marketing / legal. IT_TITLE_PATTERNS still overrides ("Sales Engineer",
    # "Marketing Data Analyst", "Finance Systems Analyst" stay).
    r'account\s*executive|sales\s*development|sdr\b|bdr\b|'
    r'business\s*development\s*(rep|representative|manager|director|lead|associate)|'
    r'sales\s*(manager|director|representative|rep|associate|specialist|executive|leader|lead)|'
    r'(enterprise|strategic|partner|channel|commercial|key|named|mid-market|smb)\s*account\s*(manager|director)|'
    r'account\s*manager|customer\s*success|renewals?\s*(manager|specialist|lead|associate)|'
    r'recruit\w*|talent\s*acquisition|sourcer\b|'
    r'people\s*(ops|operations|partner|business\s*partner|programs?|team|experience)|'
    r'hr\b|hrbp|human\s*resources|benefits\s*(specialist|analyst|manager|lead)|'
    r'payroll|workplace\s*(experience|coordinator|manager)|'
    r'office\s*(manager|coordinator|assistant)|executive\s*assistant|administrative\s*assistant|'
    r'chief\s*of\s*staff|'
    r'accountant|accounting|controller\b|bookkeep\w*|treasury|'
    r'tax\s*(manager|analyst|accountant|lead|director|specialist|associate|senior|staff)|'
    r'fp&a|financial\s*analyst|strategic\s*finance|corporate\s*development|'
    r'finance\s*(manager|analyst|director|lead|associate|partner|business\s*partner)|'
    r'investment\s*(analyst|associate)|'
    r'(general|corporate|commercial|legal|privacy|regulatory|employment|product)\s*counsel|'
    r'legal\s*(operations|ops)|contracts?\s*(manager|specialist|administrator|negotiator)|'
    r'procurement|purchasing|buyer\b|'
    r'supply\s*chain\s*(analyst|manager|planner|specialist|lead)|real\s*estate|facilities|'
    r'product\s*marketing|content\s*(marketing|strategist|writer|manager|lead)|copywriter|'
    r'brand\s*(designer|manager|marketing|strategist|lead|specialist)|'
    r'communications?\s*(manager|lead|specialist|director|coordinator)|public\s*relations|'
    r'social\s*media|field\s*market\w*|demand\s*generation|growth\s*marketing|'
    r'marketing\s*(manager|director|lead|specialist|coordinator|associate)|'
    r'events?\s*(manager|coordinator|specialist|marketing|lead)|community\s*(manager|lead)|'
    r'customer\s*(support|service)\s*(specialist|representative|associate|agent)|'
    r'onboarding\s*specialist|'
    # Other clearly non-IT
    r'idos\b|lame\b'
    r')\b',
    re.IGNORECASE
)

# IT-positive title patterns — if a title matches these, it's definitely IT
# (overrides NON_IT check in case of ambiguity)
IT_TITLE_PATTERNS = re.compile(
    r'\b('
    r'software|developer|engineer(?:ing)?|programmer|architect|devops|sre\b|'
    r'data\s*(scientist|engineer|analyst|architect)|'
    r'machine\s*learning|deep\s*learning|ai\b|ml\b|llm\b|nlp\b|'
    r'cloud|aws|azure|gcp|kubernetes|docker|terraform|'
    r'full\s*stack|front\s*end|back\s*end|frontend|backend|fullstack|'
    # The group's closing \b follows every alternative, so a bare stem can
    # never match a longer word: "product design" did not match "Product
    # Designer" and "Product Designer, Payroll" was dropped as a payroll job.
    r'product\s*(manager|owner|design\w*)|program\s*manager|'
    r'(?:ux|ui|interaction)\s*design\w*|design\s*(?:systems?|technologist)|'
    r'ux\b|ui\b|user\s*experience|user\s*interface|'
    r'cyber|security\s*(engineer|analyst|architect)|infosec|'
    r'database|dba\b|sql\b|etl\b|data\s*warehouse|'
    r'network\s*(engineer|architect|admin\w*)|sysadmin|system\s*admin\w*|'
    r'it\s*(manager|director|specialist|support|admin\w*|operations|ops|analyst|technician|generalist|coordinator|helpdesk)|'
    r'help\s*desk|technical\s*(?:\w+\s+)?support|desktop\s*support|'
    r'scrum|agile|devsecops|platform\s*engineer|'
    r'qa\b|quality\s*assurance|test\s*(engineer|automation)|sdet\b|'
    r'business\s*(analyst|intelligence)|bi\b|analytics|'
    r'erp\b|salesforce|sap\b|crm\b|'
    r'web\s*developer|mobile\s*developer|ios\b|android\b|'
    r'python|java\b|javascript|typescript|react|node\.?js|'
    r'solutions?\s*(engineer|architect|consultant)|'
    r'technical\s*(writer|lead|director|program)|'
    r'information\s*technology|information\s*systems|'
    r'site\s*reliability|reliability\s*engineer|'
    r'infrastructure|firmware|embedded|robotics|'
    r'blockchain|fintech|saas|'
    r'computer\s*scientist|'
    # Tech roles whose titles collide with the business-function patterns above
    r'systems?\s*(analyst|engineer|administrator|admin|architect|integrator)|'
    r'technical\s*account|member\s*of\s*technical\s*staff|mts\b|'
    r'research\s*(scientist|engineer)|applied\s*scientist|data\s*science|'
    # Amazon "Applied Science Internship - ..., PhD Student Science Recruiting"
    r'(?:applied|research|computer|data|decision)\s*scien\w*|'
    r'hris\b|workday|servicenow|netsuite|'
    r'solutions?\s*(engineer|architect|consultant)|'
    # Business-function technology teams (HR Tech, Finance Systems, Data &
    # Technology, Data Products) and data-center work are IT roles.
    r'(?:hr|people|finance|accounting|marketing|sales|revenue|gtm|legal|recruiting|business|payroll|tax)'
    r'\s*(?:technology|tech|systems?|tooling|automation)|'
    r'data\s*(?:&|and)\s*technology|data\s*products?|data\s*cent(?:er|re)s?'
    r')\b',
    re.IGNORECASE
)


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

        # 3. IT jobs only — drop non-IT titles (unless IT-positive overrides)
        if NON_IT_TITLE_PATTERNS.search(title) and not IT_TITLE_PATTERNS.search(title):
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
