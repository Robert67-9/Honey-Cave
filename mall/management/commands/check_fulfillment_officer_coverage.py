from django.core.management.base import BaseCommand
from mall.models import Branch, BranchAssignment


class Command(BaseCommand):
    help = 'Report active fulfillment officer coverage for all branches.'

    def handle(self, *args, **options):
        branches = (Branch.objects
                    .filter(is_active=True)
                    .select_related('fulfillment_officer')
                    .order_by('region', 'name'))

        total = branches.count()
        direct = []
        assigned = []
        uncovered = []

        for branch in branches:
            officer = branch.fulfillment_officer if (
                branch.fulfillment_officer and branch.fulfillment_officer.is_active
            ) else None

            if officer:
                direct.append((branch, officer))
                continue

            assignment = (BranchAssignment.objects
                          .filter(branch=branch, status='approved', officer__is_active=True)
                          .select_related('officer')
                          .first())
            if assignment:
                assigned.append((branch, assignment.officer, assignment.role))
            else:
                uncovered.append(branch)

        self.stdout.write(f'Total active branches: {total}')
        self.stdout.write(f'  Direct active branch officer: {len(direct)}')
        self.stdout.write(f'  Approved fallback assignment: {len(assigned)}')
        self.stdout.write(f'  No active officer coverage: {len(uncovered)}')

        if direct:
            self.stdout.write('\nBranches with direct active fulfillment officer:')
            for branch, officer in direct:
                self.stdout.write(f'  - {branch.name} ({branch.region}) → {officer.get_full_name() or officer.username}')

        if assigned:
            self.stdout.write('\nBranches covered by approved assignment:')
            for branch, officer, role in assigned:
                self.stdout.write(
                    f'  - {branch.name} ({branch.region}) → {officer.get_full_name() or officer.username} [{role}]'
                )

        if uncovered:
            self.stdout.write('\nBranches lacking active officer coverage:')
            for branch in uncovered:
                self.stdout.write(f'  - {branch.name} ({branch.region})')
            self.stdout.write(self.style.WARNING(
                '\nThese branches have no active fulfillment officer assigned via Branch.fulfillment_officer or approved BranchAssignment.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nAll active branches have at least one active fulfillment officer cover.'))
