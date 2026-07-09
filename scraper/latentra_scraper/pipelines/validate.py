"""
Validation pipeline — rejects items missing required fields,
non-IT jobs, non-US jobs, and jobs older than 90 days.
Tracks metrics for scrape_runs logging.
"""

import re
import logging
from datetime import datetime, timedelta

from scrapy.exceptions import DropItem

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
    r'landscap|roofer|mason|painter(?!\s*(ui|ux|product|visual))|'
    r'forklift|warehouse\s*associate|warehouse\s*worker|'
    r'truck\s*driver|cdl\s*driver|delivery\s*driver|'
    # Healthcare / medical
    r'nurse\b|nursing|rn\b|lpn\b|cna\b|phlebotom|radiolog|'
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
    r'product\s*(manager|owner|design)|program\s*manager|'
    r'ux\b|ui\b|user\s*experience|user\s*interface|'
    r'cyber|security\s*(engineer|analyst|architect)|infosec|'
    r'database|dba\b|sql\b|etl\b|data\s*warehouse|'
    r'network\s*(engineer|architect|admin)|sysadmin|system\s*admin|'
    r'it\s*(manager|director|specialist|support|admin)|'
    r'help\s*desk|technical\s*support|desktop\s*support|'
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
    r'computer\s*scientist'
    r')\b',
    re.IGNORECASE
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
        for state in us_states:
            if state in loc_lower:
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
            'fort bragg','fort liberty','huntsville'
        ]
        for city in us_cities:
            if city in loc_lower:
                return True

        # "Remote" with no country qualifier = allow through (likely US)
        if loc_lower in ('remote', 'remote, us', 'remote - us', 'anywhere'):
            return True
        if re.match(r'^remote\s*[-–/]\s*(us|usa|united states)', loc_lower):
            return True
        # Generic "Remote" without country = allow (most of our sources are US companies)
        if 'remote' in loc_lower and not any(x in loc_lower for x in [
            'uk', 'europe', 'emea', 'apac', 'canada', 'india', 'germany',
            'france', 'singapore', 'japan', 'australia', 'brazil', 'mexico',
            'ireland', 'london', 'berlin', 'toronto', 'sydney', 'amsterdam',
            'paris', 'dublin', 'munich', 'stockholm', 'tel aviv', 'bangalore',
            'hyderabad', 'mumbai', 'pune', 'taiwan', 'korea', 'china',
            'hong kong', 'manila', 'vietnam', 'thailand', 'poland', 'czech',
            'romania', 'bucharest', 'spain', 'italy', 'netherlands', 'switzerland',
            'basel', 'zurich', 'noida', 'gurgaon'
        ]):
            return True

        # Explicit non-US countries/cities — definitely not US
        non_us_signals = [
            'international', 'united kingdom', 'uk', 'england', 'london', 'manchester',
            'canada', 'toronto', 'vancouver', 'montreal', 'ottawa',
            'india', 'bangalore', 'hyderabad', 'mumbai', 'pune', 'noida', 'gurgaon', 'chennai',
            'germany', 'berlin', 'munich', 'hamburg', 'frankfurt',
            'france', 'paris', 'lyon',
            'japan', 'tokyo', 'osaka',
            'singapore', 'taiwan', 'taipei',
            'australia', 'sydney', 'melbourne', 'brisbane',
            'ireland', 'dublin', 'cork',
            'netherlands', 'amsterdam', 'rotterdam',
            'spain', 'madrid', 'barcelona',
            'italy', 'milan', 'rome',
            'brazil', 'sao paulo',
            'mexico', 'mexico city', 'guadalajara',
            'korea', 'seoul',
            'china', 'beijing', 'shanghai', 'shenzhen',
            'hong kong', 'israel', 'tel aviv',
            'switzerland', 'zurich', 'basel', 'geneva',
            'sweden', 'stockholm',
            'poland', 'warsaw', 'krakow', 'wroclaw',
            'czech', 'prague',
            'romania', 'bucharest',
            'portugal', 'lisbon',
            'philippines', 'manila',
            'vietnam', 'ho chi minh',
            'thailand', 'bangkok',
            'malaysia', 'kuala lumpur',
            'uae', 'dubai', 'abu dhabi',
            'saudi', 'riyadh',
            'kenya', 'nairobi',
            'south africa', 'cape town', 'johannesburg',
            'nigeria', 'lagos',
            'argentina', 'buenos aires',
            'colombia', 'bogota', 'medellin',
            'chile', 'santiago',
            'emea', 'apac', 'latam',
            'djibouti', 'kuwait',
        ]
        for sig in non_us_signals:
            if sig in loc_lower:
                return False

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
