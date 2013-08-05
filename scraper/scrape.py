################################################################################
# A script to scrape data from the Q-guide more effectively.  Inspired by      #
# David Malan's PHP script.                                                    #
#                                                                              #
# Written by Andrew Mauboussin and Josh Palay                                  #
################################################################################

from django.db import DatabaseError, IntegrityError
from lxml import etree
import re
import os
from pprint import pprint
from StringIO import StringIO
import urllib2

from models import *

# Set up some constants
DATA_DIR = 'scraper/data/'
LOG_DIR = 'scraper/log/'
ERROR_LOG = 'error.log'
WARNING_LOG = 'warning.log'
OUTPUT_LOG = 'output.log'
BASE_URL = "https://webapps.fas.harvard.edu/course_evaluation_reports/fas/"
COURSE_ID_REGEX = re.compile(r'\?course_id=(\d*)')
# (FIELD) (COURSE_NUMBER) (COURSE_TITLE)
COURSE_INFO_REGEX = re.compile(r'([\w\-\&]+)\s([\w\.\-]+):\s+(.*)')
DEPT_REGEX = re.compile(r'list\?dept=(.*?)#')
PROF_ID_REGEX = re.compile(r'([a-zA-Z\d]+):')
SCORE_BAR_REGEX = re.compile(r'\.\.\/bar_1to5-([\d\.]+)\.png')
HISTOGRAM_REGEX = re.compile(r'\.\.\/histogram\-(\d+)\-(\d+)\-(\d+)\-' + 
    r'(\d+)\-(\d+)\-\d*\.jpg')
FAILED_HISTOGRAM_REGEX = re.compile(r'(\.\.\/histogram\-\-\-\-\-\-\.jpg)')
PIN_LOGIN_REGEX = re.compile(r'Harvard University PIN Login')

TOTAL_RESPONSES_REGEX = re.compile(r'Total Responses:\s+(\d+)')
MEAN_REGEX = re.compile(r'Mean:\s+([\d\.]+)')
BREAKDOWN_REGEX = re.compile(r'\(n=(\d+)\)')
SUMMARY_STATS_REGEX = re.compile(r'Enrollment:\s*\n*\s*(\d*)\s*\n*\s*' + 
    'Evaluations:\s*\n*\s*(\d*)\s*\n*\s*' + 
    'Response Rate:\s*\n*\s*([\d\.]*)%\s*\n*\s*')
NO_COMMENTS_REGEX = re.compile(r'The response\(s\) to this question are not' + 
    ' available\. This is due to one of the following reasons\:')

# Main scraping script.  Creates opener, iterates through years/terms, 
# and calls scrape_course_list on each department listed
def scrape(cookie):
    # Clear the database
    truncate_db()
    clear_logs()

    # Set up cookies
    opener = urllib2.build_opener()
    opener.addheaders.append(('Cookie', 'JSESSIONID=' + cookie))

    # Just one year for now
    years = range(2006, 2013)
    years.reverse()
    # Term 1 = Fall, term 2 = Spring
    terms = [1, 2]

    counter = 1
    # For each term
    for year in years:
        for term in terms:
            path = "list?yearterm={0}_{1}".format(year, term) 
            dept_list_html = get_data_from_path(opener, path)

            # Get department names from HTML
            tree = etree.parse(StringIO(dept_list_html), etree.HTMLParser())
            dept_xpath = '''//div[@class="displayed_courses"]
                            //span[@class="course-block-title"]'''
            depts_elts = tree.xpath(dept_xpath)
            depts = map(lambda x: x.get('title'), depts_elts)

            # Get course list
            counter = scrape_course_list(opener, depts, term, year,
                counter)

    log('DONE!')

# Save info about every course in a semester.  Scrapes rudimentary info
# about the course, then passes it to scrape_course_data to find the rest
# of the data
# 
# opener - What it uses to download course info
#  - print output?
# depts - Departments to scrape 
# term - Spring or Fall?
# year - Which year?
# counter - How many courses have been scraped? Just used in output
def scrape_course_list(opener, depts, term, year, counter):
    course_list = []
    for dept in depts:
        # Get HTML with course list
        path = 'guide_dept?dept={0}&term={1}&year={2}'\
            .format(urllib2.quote(dept, ''), term, year)
        
        course_list_html = get_data_from_path(opener, path)

        # Parse with lxml
        tree = etree.parse(StringIO(course_list_html), etree.HTMLParser())
        # If there's no data
        if tree.getroot() is None:
            return counter
        courses = tree.xpath('//a')
        # Put data in dict, append to list
        for course in courses:
            course_id = COURSE_ID_REGEX.findall(course.get('href'))[0]
            info = COURSE_INFO_REGEX.match(course.text)
            course_dict = {
                'id': int(course_id),
                'field': info.group(1),
                'number': info.group(2),
                'title': info.group(3),
                'year': year,
                'term': term
            }
            course_dict = scrape_course_data(opener, course_dict, 
                counter)
            counter += 1
            save_course(course_dict)
    return counter

# Scrapes data about a course (including ratings), adds them to the course dict,
# then returns that course dict
def scrape_course_data(opener, course, counter):
    log('\n{0}. SCRAPING {1} {2}: {3} ({4})'.format(counter, 
        course['field'], course['number'], course['title'], course['id']))
    # Get HTML with course data
    path = 'new_course_summary.html?course_id={0}'.format(course['id'])

    course_html = get_data_from_path(opener, path)
    # Parse with lxml
    tree = etree.parse(StringIO(course_html), etree.HTMLParser())

    # Get summary stats
    try:
        stats_text = tree.xpath('//div[@id="summaryStats"]')[0].text
    except IndexError:
        course['no_data'] = True
        log_warning('NO DATA FOUND', course['id'])
        return course

    stats = SUMMARY_STATS_REGEX.findall(stats_text)[0]
    course['enrollment'] = int(stats[0])
    course['evaluations'] = int(stats[1])
    course['response_rate'] = float(stats[2])

    # #reportContent has many tables; the last two must be treated separately
    tables = tree.xpath('//div[@id="reportContent"]/table')
    course['ratings'] = []
    for table in tables[:-2]:
        course['ratings'] = course['ratings'] + \
            parse_standard_table(opener, table, course['id'])
    if len(tables) > 1:
        course['ratings'] = course['ratings'] + \
            parse_pie_charts(tables[-2])

    if tables:
        course['reasons'] = parse_reasons(tables[-1])
    else:
        course['reasons'] = {}
    course['comments'] = get_comments(opener, course)
    course['profs'] = get_profs(opener, course['id'])

    return course

def get_profs(opener, course_id):
    PROF_XPATH = '//select[@name="current_instructor_or_tf_huid_param"]/option' 
    TABLE_XPATH = '//div[@id="reportContent"]/table'
    path = 'inst-tf_summary.html?sect_num=&course_id={0}'.format(course_id)
    html = get_data_from_path(opener, path)
    tree = etree.parse(StringIO(html), etree.HTMLParser())
    prof_list = tree.xpath(PROF_XPATH)
    if not prof_list:
        log_warning('Course has no instructors', course_id)
    prof_data = []
    for prof_node in prof_list:
        prof = {}
        prof['prof_id'] = PROF_ID_REGEX.findall(prof_node.attrib['value'])[0]
        names = prof_node.text.split(',')
        if len(names) > 2:
            log_warning('More than one comma in instructor name', course_id)
        prof['first'] = names[1].strip()
        prof['last'] = names[0].strip()
        log('FOUND INSTRUCTOR {0} {1} ({2})'.format(prof['first'], prof['last'], prof['prof_id']))
        tables = tree.xpath(TABLE_XPATH)
        if not tables:
            log_warning('NO DATA FOUND FOR {0} {1} ({2})'.format(
                prof['first'], prof['last'], prof['prof_id']), course_id)
            prof['ratings'] = []
        else:
            for table in tables:
                prof['ratings'] = parse_standard_table(opener, table, course_id)
        prof_data.append(prof)

    return prof_data


def parse_standard_table(opener, table, course_id):
    log('PARSING NEW TABLE')
    # Find rows
    rows = table.xpath('.//tr')
    ratings = []
    for row in rows[1:-2]:
        rating = {}
        cells = row.xpath('./td')

        # Get category
        rating['category'] = cells[0].xpath('./strong')[0].text
        if rating['category'] == 'Workload (hours per week)':
            rating['category'] = 'Workload'
        log_msg = rating['category'] + ': '

        rating['num_responses'] = int(cells[1].text)
        if not cells[2].xpath('.//img'):
            rating['value']  = None
            rating['ones']   = 0
            rating['twos']   = 0
            rating['threes'] = 0
            rating['fours']  = 0
            rating['fives']  = 0
            log(log_msg + 'None')
            continue
        img_src = cells[2].xpath('.//img')[0].get('src')
        rating['value'] = float(SCORE_BAR_REGEX.match(img_src).group(1))
        log(log_msg + str(rating['value']))
        histogram_url = cells[3].xpath('.//a')[0].get('href')

        add_score_breakdown(opener, rating, histogram_url, course_id)
        ratings.append(rating)
    return ratings

def parse_pie_charts(table):
    # 0 and 2 are the only two rows with actual data in them
    log('PARSING NEW TABLE')
    ratings = []
    for row in table.xpath('tr')[0:2]:
        rating = {}
        if not row.xpath('.//strong'):
            continue

        rating['category'] = row.xpath('.//strong')[0].text
        log_msg = rating['category'] + ': '
        row_html = etree.tostring(row)

        try:
            rating['num_responses'] = \
                int(TOTAL_RESPONSES_REGEX.findall(row_html)[0])
        except IndexError:
            rating['num_responses'] = 0
            rating['value']  = None
            rating['ones']   = 0
            rating['twos']   = 0
            rating['threes'] = 0
            rating['fours']  = 0
            rating['fives']  = 0
            log(log_msg + 'None')
            continue

        rating['value'] = float(MEAN_REGEX.findall(row_html)[0])
        breakdown = BREAKDOWN_REGEX.findall(row_html)

        log(log_msg + str(map(lambda x: int(x), list(breakdown))))

        rating['ones']   = int(breakdown[0])
        rating['twos']   = int(breakdown[1])
        rating['threes'] = int(breakdown[2])
        rating['fours']  = int(breakdown[3])
        rating['fives']  = int(breakdown[4])

        ratings.append(rating)
    return ratings

# Parses reasons for taking curse
def parse_reasons(table):
    log('PARSING REASONS')
    reasons = {}
    for row in table.xpath('tr')[1:]:
        reason = row.xpath('td')[-1].text
        log_msg = reason + ': '
        reasons[reason] = int(row.xpath('td')[-2].text)
        log(log_msg + str(reasons[reason]))

    return reasons

# Adds the score breakdown to the rating dict
def add_score_breakdown(opener, rating, histogram_url, course_id):
    html = get_data_from_path(opener, histogram_url)
    if FAILED_HISTOGRAM_REGEX.findall(html):
        os.remove(DATA_DIR + histogram_url)
        log_error("Score breakdown page unexpectedly displays no breakdown " +\
                  "(path: {0})".format(histogram_url), course_id)
        rating['ones']   = 0 
        rating['twos']   = 0 
        rating['threes'] = 0 
        rating['fours']  = 0 
        rating['fives']  = 0 
        return
    scores = HISTOGRAM_REGEX.findall(html)[0]
    log(str(map(lambda x: int(x), list(scores))))
    rating['ones']   = int(scores[0])
    rating['twos']   = int(scores[1])
    rating['threes'] = int(scores[2])
    rating['fours']  = int(scores[3])
    rating['fives']  = int(scores[4])

def get_comments(opener, course):
    log('GETTING COMMENTS')
    path = 'view_comments.html?course_id={0}'.format(course['id'])
    comments_html = get_data_from_path(opener, path)
    if NO_COMMENTS_REGEX.findall(comments_html):
        if course['year'] >= 2007:
            log_warning('No comments found for course taught after 2007', course['id'])
        else:
            log('NO COMMENTS')
        return []
    log('GETTING COMMENTS WITH &qid=1487')
    path = path + '&qid=1487'
    comments_html = get_data_from_path(opener, path)
    tree = etree.parse(StringIO(comments_html), etree.HTMLParser())
    comments_xpath = '//div[@id="responseBlock"]/div[@class="response"]/p'
    return map(lambda x: x.text, tree.xpath(comments_xpath))

def save_course(course):
    # STEP 0: Throw out courses with no data
    if 'no_data' in course.keys() and course['no_data']:
        log('NO DATA TO SAVE FOR COURSE')
        return

    # STEP 1: Save Field
    log_msg = 'SAVING FIELD {0}... '.format(course['field'])
    f = None
    try:
        f = Field(abbreviation=course['field'], name='')
        f.save()
        log(log_msg + 'FIELD SAVED')
    except DatabaseError as e:
        log(str(e))
        f = Field.objects.filter(abbreviation=course['field'])[0]
        log(log_msg + 'FIELD ALREADY EXISTS')


    # STEP 2: Save Course
    log_msg = 'SAVING COURSE... '
    c = None
    try:
        c = Course(
            field         = f,
            number        = course['number'],
            title         = course['title'],
            course_id     = course['id'],
            year          = course['year'],
            term          = course['term'],
            enrollment    = course['enrollment'],
            evaluations   = course['evaluations'],
            response_rate = course['response_rate']
        )
        c.save()
        log(log_msg + 'COURSE SAVED')
    except DatabaseError as e:
        log(str(e))
        c = Course.objects.filter(course_id=course['id'])[0]
        log(log_msg + 'COURSE ALREADY EXISTS')

    # STEP 3: Save Comments
    for comment in course['comments']:
        log_msg = 'SAVING COMMENT... '
        try:
            Comment(course=c, comment=comment).save()
            log(log_msg + 'COMMENT SAVED')
        except DatabaseError as e:
            log(str(e))
            log(log_msg + 'COMMENT ALREADY EXISTS')

    # Save Ratings
    for r in course['ratings']:
        save_rating(c, r)
            
    # Save Profs
    for p in course['profs']:
        log_msg = 'SAVING INSTRUCTOR {0} {1} ({2})... '.format(p['first'], p['last'], p['prof_id'])
        try:
            i = Instructor(
                course = c,
                prof_id = p['prof_id'],
                first = p['first'],
                last = p['last']
            )
            i.save()
            log(log_msg + 'INSTRUCTOR SAVED')
        except DatabaseErorr as e:
            log(str(e))
            log(log_msg + 'INSTRUCTOR ALREADY EXISTS')
            i = Instructor.objects.filter(course=c, prof_id=p['prof_id'])[0]
        for r in p['ratings']:
            save_rating(i, r)

    reasons_dict = {
        'Elective':
            'Elective',
        'Concentration or Department Requirement':
            'Concentration or Department Requirement',
        'Secondary Field or Language Citation Requirement':
            'Secondary Field or Language Citation Requirement',
        'Undergraduate Core or General Education Requirement':
            'Undergraduate Core or General Education Requirement',
        'Expository Writing Requirement':
            'Expository Writing Requirement',
        'Foreign Language Requirement':
            'Foreign Language Requirement',
        'Pre-Med Requirement':
            'Pre-Med Requirement',
        'Undergraduate Core Requirement': 
            'Undergraduate Core or General Education Requirement',
        'Concentration/Program Requirement':
            'Concentration or Department Requirement'
    }

    for r in course['reasons'].keys():
        log_msg = 'SAVING REASON {0}... '.format(reasons_dict[r])
        try:
            Reason(course=c, reason=reasons_dict[r], 
                number=course['reasons'][r]).save()
            log(log_msg + 'REASON SAVED')
        except DatabaseError as e:
            log(str(e))
            log(log_msg + 'REASON ALREADY EXISTS')

def save_rating(rated_object, rating):
    log_msg = 'SAVING RATING {0}... '.format(rating['category'])
    try:
        Rating(
            rated_object  = rated_object,
            category      = rating['category'],
            value         = rating['value'],
            num_responses = rating['num_responses'],
            ones          = rating['ones'],
            twos          = rating['twos'],
            threes        = rating['threes'],
            fours         = rating['fours'],
            fives         = rating['fives']
        ).save()
        log(log_msg + 'RATING SAVED')
    except DatabaseError as e:
        log(str(e))
        log(log_msg + 'RATING ALREADY EXISTS')

# Gets data from file in DATA_DIR, or downloads and saves it
def get_data_from_path(opener, path):
    try:
        contents = open(DATA_DIR + path).read()
        if PIN_LOGIN_REGEX.findall(contents):
            raise IOError
    except IOError:
        url = BASE_URL + path
        contents = opener.open(url).read()
        contents = contents.decode('utf-8')\
            .encode('ascii', 'ignore')

        # Check to see if cookie is still good
        if PIN_LOGIN_REGEX.findall(contents):
            raise ValueError('Cookie no longer valid')

        # Save contents
        f = open(DATA_DIR + path, 'w')
        f.write(contents)
        f.close()
        
    return contents

def truncate_db():
    Rating.objects.all().delete()
    Field.objects.all().delete()
    Course.objects.all().delete()
    Comment.objects.all().delete()
    Reason.objects.all().delete()
    Instructor.objects.all().delete()

def clear_logs():
    for log_name in [OUTPUT_LOG, ERROR_LOG, WARNING_LOG]:
        open(LOG_DIR + log_name, 'w').close()

# LOGGING UTILITIES
def log(msg):
    with open(LOG_DIR + OUTPUT_LOG, 'a') as f:
        f.write(msg + '\n') 

def log_error(msg, course_id):
    msg = 'ERROR: ' + str(course_id) + ': ' + msg
    log(msg)
    with open(LOG_DIR + ERROR_LOG, 'a') as f:
        f.write(msg + '\n') 

def log_warning(msg, course_id):
    msg = 'WARNING: ' + str(course_id) + ': ' + msg
    log(msg)
    with open(LOG_DIR + WARNING_LOG, 'a') as f:
        f.write(msg + '\n') 
