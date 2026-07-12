from django.db import models

# Create your models here.
class Catagory(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField( max_length=200 , unique=True )
    parent = models.ForeignKey('self',on_delete=models.CASCADE)
    def __str__(self):
        return self.name