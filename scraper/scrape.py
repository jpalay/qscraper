################################################################################
# A script to scrape data from the Q-guide more effectively.  Inspired by (i.e #
# code heavily borrowed from) David Malan's PHP script.                        #
#                                                                              #
# Written by Andrew Mauboussin and Josh Palay                                  #
################################################################################

from models import *

import re
import urllib2

from lxml import etree
from StringIO import StringIO

import os.path
from pprint import pprint

# Set up some constants
DATA_DIR = 'scraper/data/'
BASE_URL = "https://webapps.fas.harvard.edu/course_evaluation_reports/fas/"
COURSE_ID_REGEX = re.compile(r'\?course_id=(\d*)')
# (FIELD) (COURSE_NUMBER) (COURSE_TITLE)
COURSE_INFO_REGEX = re.compile(r'([\w\-\&]+)\s([\w\.\-]+):\s+(.*)')
DEPT_REGEX = re.compile(r'list\?dept=(.*?)#')
SCORE_BAR_REGEX = re.compile(r'\.\.\/bar_1to5-([\d\.]+)\.png')
HISTOGRAM_REGEX = re.compile(r'\.\.\/histogram\-(\d+)\-(\d+)\-(\d+)\-' + 
    r'(\d+)\-(\d+)\-\d*\.jpg')

TOTAL_RESPONSES_REGEX = re.compile(r'Total Responses:\s+(\d+)')
MEAN_REGEX = re.compile(r'Mean:\s+([\d\.]+)')
BREAKDOWN_REGEX = re.compile(r'\(n=(\d+)\)')
SUMMARY_STATS_REGEX = re.compile(r'Enrollment:\s*\n*\s*(\d*)\s*\n*\s*' + 
    'Evaluations:\s*\n*\s*(\d*)\s*\n*\s*' + 
    'Response Rate:\s*\n*\s*([\d\.]*)%\s*\n*\s*')
NO_COMMENTS_REGEX = re.compile(r'The response\(s\) to this question are not' + 
    ' available\. This is due to one of the following reasons\:')

# STEP 2: SCRAPE AND SAVE DATA
def scrape(cookie, verbose=True):
    # Set up cookies
    opener = urllib2.build_opener()
    opener.addheaders.append(('Cookie', 'JSESSIONID=' + cookie))

    # Just one year for now
    years = range(2006, 2013)
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
            courses = scrape_course_list(opener, verbose, depts, term, year, 
                counter)
            pprint(courses)

def scrape_course_list(opener, verbose, depts, term, year, counter):
    course_list = []
    for dept in depts:
        # Get HTML with course list
        path = 'guide_dept?dept={0}&term={1}&year={2}'\
            .format(urllib2.quote(dept, ''), term, year)
        
        course_list_html = get_data_from_path(opener, path)
        # Parse with lxml
        tree = etree.parse(StringIO(course_list_html), etree.HTMLParser())
        courses = tree.xpath('//a')
        # Put data in dict, append to list
        for course in courses:
            course_id = COURSE_ID_REGEX.findall(course.get('href'))[0]
            # if course in db
            info = COURSE_INFO_REGEX.match(course.text)
            course_dict = {
                'id': int(course_id),
                'field': info.group(1),
                'number': info.group(2),
                'title': info.group(3),
                'year': year,
                'term': term
            }
            course_dict = scrape_course_data(opener, verbose, course_dict, 
                counter)
            counter = counter + 1
            save_course(verbose, course_dict)
            # course_list.append(course_dict)
    # return course_list

def scrape_course_data(opener, verbose, course, counter):
    if verbose:
        print '\n{0}. SCRAPING {1} {2}: {3} ({4})'.format(counter, 
            course['field'], course['number'], course['title'], course['id'])
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
        print 'NO DATA FOUND'
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
            parse_standard_table(opener, verbose, table)
    if len(tables) > 1:
        course['ratings'] = course['ratings'] + \
            parse_pie_charts(verbose, tables[-2])

    if tables:
        course['reasons'] = parse_reasons(verbose, tables[-1])
    else:
        course['reasons'] = {}
    course['comments'] = get_comments(opener, verbose, course)

    return course

def parse_standard_table(opener, verbose, table):
    if verbose:
        print 'PARSING NEW TABLE'
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
        if verbose:
            print rating['category'] + ':',

        rating['num_responses'] = int(cells[1].text)
        if not cells[2].xpath('.//img'):
            rating['value']  = None
            rating['ones']   = 0
            rating['twos']   = 0
            rating['threes'] = 0
            rating['fours']  = 0
            rating['fives']  = 0
            if verbose:
                print 'None'
            continue
        img_src = cells[2].xpath('.//img')[0].get('src')
        rating['value'] = float(SCORE_BAR_REGEX.match(img_src).group(1))
        histogram_url = cells[3].xpath('.//a')[0].get('href')

        add_score_breakdown(opener, verbose, rating, histogram_url)
        ratings.append(rating)
    return ratings

def parse_pie_charts(verbose, table):
    # 0 and 2 are the only two rows with actual data in them
    if verbose:
        print 'PARSING NEW TABLE'
    ratings = []
    for i in [0, 2]:
        rating = {}
        row = table.xpath('tr')[i]
        if not row.xpath('.//strong'):
            continue

        rating['category'] = row.xpath('.//strong')[0].text
        if verbose:
            print rating['category'] + ':',
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
            if verbose:
                print 'None'
            continue

        rating['value'] = float(MEAN_REGEX.findall(row_html)[0])
        breakdown = BREAKDOWN_REGEX.findall(row_html)

        if verbose:
            print map(lambda x: int(x), list(breakdown))

        rating['ones']   = int(breakdown[0])
        rating['twos']   = int(breakdown[1])
        rating['threes'] = int(breakdown[2])
        rating['fours']  = int(breakdown[3])
        rating['fives']  = int(breakdown[4])

        ratings.append(rating)
    return ratings

# Parses reasons for taking curse
def parse_reasons(verbose, table):
    if verbose:
        print 'PARSING REASONS'
    reasons = {}
    for row in table.xpath('tr')[1:]:
        reason = row.xpath('td')[-1].text
        if verbose:
            print reason + ':',
        reasons[reason] = int(row.xpath('td')[-2].text)
        if verbose:
            print reasons[reason]

    return reasons

# Adds the score breakdown to the rating dict
def add_score_breakdown(opener, verbose, rating, histogram_url):
    html = get_data_from_path(opener, histogram_url)
    scores = HISTOGRAM_REGEX.findall(html)[0]
    if verbose:
        print map(lambda x: int(x), list(scores))
    rating['ones']   = int(scores[0])
    rating['twos']   = int(scores[1])
    rating['threes'] = int(scores[2])
    rating['fours']  = int(scores[3])
    rating['fives']  = int(scores[4])

def get_comments(opener, verbose, course):
    if verbose:
        print 'GETTING COMMENTS'
    path = 'view_comments.html?course_id={0}'.format(course['id'])
    comments_html = get_data_from_path(opener, path)
    if NO_COMMENTS_REGEX.findall(comments_html):
        if verbose:
            print 'NO COMMENTS'
        return []
    if verbose:
        print 'GETTING COMMENTS WITH &qid=1487'
    path = path + '&qid=1487'
    comments_html = get_data_from_path(opener, path)
    tree = etree.parse(StringIO(dept_list_html), etree.HTMLParser())
    comments_xpath = '//div[@id="responseBlock"]/div[@class="response"]/p'
    return map(lambda x: x.text, tree.xpath(comments_xpath))

def save_course(verbose, course):
    # STEP 0: Throw out courses with no data
    if 'no_data' in course.keys() and course['no_data']:
        if verbose:
            print 'NO DATA TO SAVE FOR COURSE'
        return

    # STEP 1: Save Field
    if verbose:
        print 'SAVING FIELD {0}...'.format(course['field']),
    if Field.objects.filter(abbreviation__iexact=course['field']):
        if verbose:
            print 'FIELD ALREADY EXISTS'
        f = Field.objects.get(abbreviation__iexact=course['field'])
    else:
        f = Field(abbreviation=course['field'], name='')
        f.save()
        if verbose:
            print 'FIELD SAVED'

    if verbose:
        print 'SAVING COURSE...',

    # STEP 2: Save Course
    if Course.objects.filter(course_id=course['id']):
        if verbose:
            print 'COURSE ALREADY EXISTS'
        c = Course.objects.get(course_id=course['id'])
    else:
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
        if verbose:
            print 'COURSE SAVED'

    # STEP 3: Save Comments
    for comment in course['comments']:
        if verbose:
            print 'SAVING COMMENT...',
        if Comment.objects.filter(course=c, comment=comment):
            if verbose:
                print 'COMMENT ALREADY EXISTS'
            continue
        else:
            comment_obj = Comment(course=c, comment=comment)
            comment_obj.save()
            if verbose:
                print 'COMMENT SAVED'

    # TODO: Save Ratings
    for r in course['ratings']:
        if verbose:
            print 'SAVING RATING {0}...'.format(r['category']),
        if c.ratings.filter(category__iexact=r['category']):
            if verbose:
                print 'RATING ALREADY EXISTS'
            continue
        rating = Rating(
                    rated_object  = c,
                    category      = r['category'],
                    value         = r['value'],
                    num_responses = r['num_responses'],
                    ones          = r['ones'],
                    twos          = r['twos'],
                    threes        = r['threes'],
                    fours         = r['fours'],
                    fives         = r['fives']
        )
        rating.save()
        if verbose:
            print 'RATING SAVED'
    # TODO: Save Reasons
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
        if verbose:
            print 'SAVING REASON {0}...'.format(reasons_dict[r]),
        if c.reasons().filter(reason=reasons_dict[r]):
            if verbose:
                print 'REASON ALREADY EXISTS'
            continue
        reason = Reason(course=c, reason=reasons_dict[r], 
            number=course['reasons'][r])
        reason.save()
        if verbose:
            print 'REASON SAVED'

# Gets data from file in DATA_DIR, or downloads and saves it
def get_data_from_path(opener, path):
    try:
        contents = open(DATA_DIR + path).read()
    except IOError:
        url = BASE_URL + path
        contents = opener.open(url).read()
        contents = contents.decode('utf-8')\
            .encode('ascii', 'ignore')

        # Save contents
        f = open(DATA_DIR + path, 'w')
        f.write(contents)
        f.close()
        
    return contents



















