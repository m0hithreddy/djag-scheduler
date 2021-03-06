"""Task Dependency model"""

from django.db import models
from django.db.models import signals
from django.core.exceptions import ValidationError

from collections import defaultdict

from toposort import toposort
from toposort import CircularDependencyError

from .periodic_task_model import PeriodicTask
from .user_action_model import UserAction
import djag_scheduler.models.user_action_model as action_choices


class TaskDependency(models.Model):
    """Model representing Djag-Scheduler task dependencies"""

    depender = models.ForeignKey(
        PeriodicTask, on_delete=models.CASCADE, related_name='depender_task',
        verbose_name='Depender', help_text='Task Dependent on Dependee'
    )
    dependee = models.ForeignKey(
        PeriodicTask, on_delete=models.CASCADE, related_name='dependee_task',
        verbose_name='Dependee', help_text='Task Dependent by Depender'
    )
    future_depends = models.BooleanField(
        default=False, verbose_name='Future Dependency',
        help_text='Depender is Future Dependent on Dependee'
    )
    change_dt = models.DateTimeField(
        auto_now=True, verbose_name='Change Date',
        help_text='Date Time at which dependency is created/changed'
    )

    class Meta:
        """Table Information"""

        verbose_name = 'Task Dependency'
        verbose_name_plural = 'Task Dependencies'
        constraints = [
            models.UniqueConstraint(
                name='task_set',
                fields=['depender', 'dependee']
            )
        ]

    def clean(self):
        """Clean model data"""
        if self.depender == self.dependee and not self.future_depends:
            raise ValidationError('A task can only future depend on itself')

        # Detect cycles

        # Group tasks by depender
        task_groups = defaultdict(set)
        for task_depend in TaskDependency.objects.all():
            task_groups[task_depend.depender.id].add(
                -task_depend.dependee.id if task_depend.future_depends
                else task_depend.dependee.id
            )

        task_dag = {}
        for depender, dependee in task_groups.items():
            task_dag[depender] = dependee

        # Add new dependency
        try:
            task_dag[self.depender.id].add(
                -self.dependee.id if self.future_depends
                else self.dependee.id
            )
        except KeyError:
            if self.future_depends:
                task_dag[self.depender.id] = {-self.dependee.id}
            else:
                task_dag[self.depender.id] = {self.dependee.id}

        try:
            _ = tuple(toposort(task_dag))
        except CircularDependencyError:
            raise ValidationError('Task-Dependency creates a cycle in DAG')

    def save(self, *args, **kwargs):
        """Save model data"""
        self.full_clean()

        # Call super().save() only when there are real changes
        if not self.pk:
            # Object created for the first time
            super().save(*args, **kwargs)
        else:
            cls = self.__class__
            old = cls.objects.get(pk=self.pk)

            # Check for changes (use update_fields if set)
            for field in (kwargs.get('update_fields') or [field.name for field in cls._meta.get_fields()]):
                try:
                    if getattr(old, field) != getattr(self, field):
                        try:
                            super().save(*args, **kwargs)
                        except Exception as exc:
                            raise exc
                        break
                except: # noqa
                    raise ValueError('Failed to compare the field ' + field + ' between the current and old model')

    def __str__(self):
        return '{0} --{1} {2}'.format(
            self.depender.name,
            "D+" if self.future_depends else "D",
            self.dependee.name
        )

    @classmethod
    def insert_dependency_change(cls, instance, *args, **kwargs):
        """Insert Task Deleted record into UserAction"""
        if not isinstance(instance, TaskDependency):
            return

        user_action = UserAction(
            action=action_choices.DEPENDENCY_CHANGED,
            payload=dict(
                schedule_id=instance.id,
                schedule=str(instance)
            )
        )
        user_action.save()


signals.pre_save.connect(TaskDependency.insert_dependency_change, sender=TaskDependency)
signals.pre_delete.connect(TaskDependency.insert_dependency_change, sender=TaskDependency)
