from django.core.management.base import BaseCommand, CommandError
from scraper import scrape

class Command(BaseCommand):
    args = '<cookie>'
    help = 'Runs the Q guide scraper'

    def handle(self, *args, **options):
        cookie = ''
        if len(args):
            cookie = args[0]
        self.stdout.write('Scraping Q guide with cookie {0}'.format(cookie))
        scrape.scrape(cookie)
        self.stdout.write('Done scraping!')
