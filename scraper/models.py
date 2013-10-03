from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic

class Rating(models.Model):
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    rated_object = generic.GenericForeignKey('content_type', 'object_id')

    category      = models.CharField(max_length=1024)
    value       = models.DecimalField(decimal_places=2, max_digits=5, null=True)
    num_responses = models.PositiveIntegerField()
    ones          = models.IntegerField()
    twos          = models.IntegerField()
    threes        = models.IntegerField()
    fours         = models.IntegerField()
    fives         = models.IntegerField()

    def __unicode__(self):
        return self.category + ": " + str(self.value)

    # Doesn't work because category is too long
    # class Meta:
    #     unique_together = ('object_id', 'category')

class Field(models.Model):
    abbreviation = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    def __unicode__(self):
        if self.name != '':
            return self.name
        else:
            return self.abbreviation

class Course(models.Model):
    field = models.ForeignKey(Field)
    number = models.CharField(max_length=128)
    title = models.CharField(max_length=512)

    def __unicode__(self):
        return self.field.__unicode__() +' '+self.number+': '+self.title

    class Meta:
        unique_together = ('field', 'number')

class CourseInstance(models.Model):
    course = models.ForeignKey(Course)
    qcourse_id = models.IntegerField(unique=True)
    # cat_num = models.CharField(max_length=765)
    year = models.IntegerField()
    term = models.IntegerField()
    enrollment = models.IntegerField()
    evaluations = models.IntegerField()
    response_rate = models.DecimalField(decimal_places=2, null=True, 
        max_digits=8, db_column='ResponseRate', blank=True)
    ratings = generic.GenericRelation(Rating)

    def __unicode__(self):
        return self.course.field.__unicode__() +' '+self.course.number+': '+self.course.title

    #get the text representing this course's term
    def term_text(self):
        if self.term == 1:
            return 'Fall'
        elif self.term==2:
            return 'Spring'
        else: return 'Unknown'
        
    @property
    def reasons(self):
        return Reason.objects.filter(course=self)

    @property
    def comments(self):
        return Comment.objects.filter(course=self)


class Comment(models.Model):
    course = models.ForeignKey(CourseInstance)
    comment = models.TextField(max_length=10000)

    def __unicode__(self):
        return self.course.__unicode__()

    # Doesn't work because comment is too long
    # class Meta:
    #     unique_together = ('course', 'comment')

class Reason(models.Model):
    REASON_CHOICES = (
        ('Elective', 
            'Elective'),
        ('Concentration or Department Requirement', 
            'Concentration or Department Requirement'),
        ('Secondary Field or Language Citation Requirement',
            'Secondary Field or Language Citation Requirement'),
        ('Undergraduate Core or General Education Requirement',
            'Undergraduate Core or General Education Requirement'),
        ('Expository Writing Requirement',
            'Expository Writing Requirement'),
        ('Foreign Language Requirement',
            'Foreign Language Requirement'),
        ('Pre-Med Requirement',
            'Pre-Med Requirement')
    )

    course = models.ForeignKey(CourseInstance)
    reason = models.CharField(max_length=128, choices=REASON_CHOICES)
    number = models.IntegerField()

    def __unicode__(self):
        return self.reason + ': ' + str(self.number)
    
    class Meta:
        unique_together = ('course', 'reason')

class Instructor(models.Model):
    # One prof_id per professor (not unique)
    prof_id = models.CharField(max_length=255)
    first = models.CharField(max_length=384)
    last = models.CharField(max_length=384)

    def __unicode__(self):
        return self.first + ' ' + self.last
    
class InstructorCourseInstanceRelation(models.Model):
    instructor = models.ForeignKey(Instructor)
    course_instance = models.ForeignKey(CourseInstance)
    ratings = generic.GenericRelation(Rating)

    def __unicode__(self):
        return self.instructor.first + ' ' + self.instructor.last

    class Meta:
        unique_together = ('instructor', 'course_instance')

# class InstructorInstance(models.Model):
#     instructor = models.ForeignKey(Instructor)
#     course = models.ForeignKey(Course)
#     ratings = generic.GenericRelation(Rating)
# 
#     def __unicode__(self):
#         return self.instructor.first + ' ' + self.instructor.last

    # class Meta:
    #     unique_together('instructor', 'course')
