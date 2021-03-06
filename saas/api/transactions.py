# Copyright (c) 2018, DjaoDjin inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from collections import OrderedDict

import dateutil
from django.db.models import Q
from extra_views.contrib.mixins import SearchableListMixin, SortableListMixin
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, DestroyAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import TransactionSerializer
from ..mixins import DateRangeMixin, OrganizationMixin, ProviderMixin
from ..models import Transaction, sum_dest_amount, sum_orig_amount
from ..backends import ProcessorError


class BalancePagination(PageNumberPagination):
    """
    Decorate the results of an API call with balance on an account
    containing *selector*.
    """

    def paginate_queryset(self, queryset, request, view=None):
        self.ends_at = view.ends_at
        if view.selector is not None:
            dest_totals = sum_dest_amount(queryset.filter(
                dest_account__icontains=view.selector))
            orig_totals = sum_orig_amount(queryset.filter(
                orig_account__icontains=view.selector))
        else:
            dest_totals = sum_dest_amount(queryset)
            orig_totals = sum_orig_amount(queryset)
        self.balance_amount = dest_totals['amount'] - orig_totals['amount']
        self.balance_unit = dest_totals['unit']
        return super(BalancePagination, self).paginate_queryset(
            queryset, request, view=view)

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('ends_at', self.ends_at),
            ('balance', self.balance_amount),
            ('unit', self.balance_unit),
            ('count', self.page.paginator.count),
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data)
        ]))


class StatementBalancePagination(PageNumberPagination):
    """
    Decorate the results of an API call with the balance as shown
    in an organization statement.
    """

    def paginate_queryset(self, queryset, request, view=None):
        self.ends_at = view.ends_at
        self.balance_amount, self.balance_unit \
            = Transaction.objects.get_statement_balance(view.organization)
        return super(StatementBalancePagination, self).paginate_queryset(
            queryset, request, view=view)

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('ends_at', self.ends_at),
            ('balance', self.balance_amount),
            ('unit', self.balance_unit),
            ('count', self.page.paginator.count),
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data)
        ]))


class TotalPagination(PageNumberPagination):

    def paginate_queryset(self, queryset, request, view=None):
        self.ends_at = view.ends_at
        self.totals = view.totals
        return super(TotalPagination, self).paginate_queryset(
            queryset, request, view=view)

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('ends_at', self.ends_at),
            ('total', self.totals['amount']),
            ('unit', self.totals['unit']),
            ('count', self.page.paginator.count),
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data)
        ]))


class TotalAnnotateMixin(object):

    def get_queryset(self):
        queryset = super(TotalAnnotateMixin, self).get_queryset()
        self.totals = sum_orig_amount(queryset)
        return queryset


class TransactionFilterMixin(DateRangeMixin, SearchableListMixin):
    """
    ``Transaction`` list result of a search query, filtered by dates.
    """

    search_fields = ['descr',
                     'orig_organization__full_name',
                     'dest_organization__full_name']


class SmartTransactionListMixin(SortableListMixin, TransactionFilterMixin):
    """
    ``Transaction`` list which is also searchable and sortable.
    """

    sort_fields_aliases = [('descr', 'description'),
                           ('dest_amount', 'amount'),
                           ('dest_organization__slug', 'dest_organization'),
                           ('dest_account', 'dest_account'),
                           ('orig_organization__slug', 'orig_organization'),
                           ('orig_account', 'orig_account'),
                           ('created_at', 'created_at')]


class TransactionQuerysetMixin(object):

    def get_queryset(self):
        self.selector = self.request.GET.get('selector', None)
        if self.selector is not None:
            return Transaction.objects.filter(
                Q(dest_account__icontains=self.selector)
                | Q(orig_account__icontains=self.selector))
        return Transaction.objects.all()


class TransactionListAPIView(SmartTransactionListMixin,
                             TransactionQuerysetMixin, ListAPIView):
    """
    GET queries all ``Transaction`` recorded in the ledger.

    The queryset can be further filtered to a range of dates between
    ``start_at`` and ``ends_at``.

    The queryset can be further filtered by passing a ``q`` parameter.
    The value in ``q`` will be matched against:

      - Transaction.descr
      - Transaction.orig_organization.full_name
      - Transaction.dest_organization.full_name

    The result queryset can be ordered by:

      - Transaction.created_at
      - Transaction.descr
      - Transaction.dest_amount

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/transactions?start_at=2015-07-05T07:00:00.000Z\
&o=date&ot=desc

    **Example response**:

    .. sourcecode:: http

        {
            "ends_at": "2017-03-30T18:10:12.962859Z",
            "balance": 11000,
            "unit": "usd",
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "created_at": "2017-02-01T00:00:00Z",
                    "description": "Charge for 4 periods",
                    "amount": "($356.00)",
                    "is_debit": true,
                    "orig_account": "Liability",
                    "orig_organization": "xia",
                    "orig_amount": 112120,
                    "orig_unit": "usd",
                    "dest_account": "Funds",
                    "dest_organization": "stripe",
                    "dest_amount": 112120,
                    "dest_unit": "usd"
                }
            ]
        }
    """
    serializer_class = TransactionSerializer
    pagination_class = BalancePagination


class BillingsQuerysetMixin(OrganizationMixin):

    def get_queryset(self):
        """
        Get the list of transactions for this organization.
        """
        return Transaction.objects.by_customer(self.organization)


class BillingsAPIView(SmartTransactionListMixin,
                      BillingsQuerysetMixin, ListAPIView):
    """
    GET queries all ``Transaction`` associated to ``:organization`` while
    the organization acts as a subscriber in the relation.

    The queryset can be further filtered to a range of dates between
    ``start_at`` and ``ends_at``.

    The queryset can be further filtered by passing a ``q`` parameter.
    The value in ``q`` will be matched against:

      - Transaction.descr
      - Transaction.orig_organization.full_name
      - Transaction.dest_organization.full_name

    The result queryset can be ordered by:

      - Transaction.created_at
      - Transaction.descr
      - Transaction.dest_amount

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/xia/billings?start_at=2015-07-05T07:00:00.000Z\
&o=date&ot=desc

    **Example response**:

    .. sourcecode:: http

        {
            "count": 1,
            "next": null,
            "previous": null,
            "balance": 11000,
            "unit": "usd",
            "results": [
                {
                    "created_at": "2015-08-01T00:00:00Z",
                    "description": "Charge for 4 periods",
                    "amount": "($356.00)",
                    "is_debit": true,
                    "orig_account": "Liability",
                    "orig_organization": "xia",
                    "orig_amount": 112120,
                    "orig_unit": "usd",
                    "dest_account": "Funds",
                    "dest_organization": "stripe",
                    "dest_amount": 112120,
                    "dest_unit": "usd"
                }
            ]
        }
    """
    serializer_class = TransactionSerializer
    pagination_class = StatementBalancePagination


class ReceivablesQuerysetMixin(ProviderMixin):

    def get_queryset(self):
        """
        Get the list of transactions for this organization.
        """
        return self.provider.receivables().filter(orig_amount__gt=0)


class ReceivablesListAPIView(SortableListMixin, TotalAnnotateMixin,
                             TransactionFilterMixin, ReceivablesQuerysetMixin,
                             ListAPIView):
    """
    GET queries all receivables for a provider.

    The queryset can be further filtered to a range of dates between
    ``start_at`` and ``ends_at``.

    The queryset can be further filtered by passing a ``q`` parameter.
    The value in ``q`` will be matched against:

      - Transaction.descr
      - Transaction.orig_organization.full_name
      - Transaction.dest_organization.full_name

    The result queryset can be ordered by:

      - Transaction.created_at
      - Transaction.descr
      - Transaction.dest_amount

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/cowork/receivables?start_at=2015-07-05T07:00:00.000Z\
&o=date&ot=desc

    **Example response**:

    .. sourcecode:: http

        {
            "count": 1,
            "total": "112120",
            "unit": "usd",
            "next": null,
            "previous": null,
            "results": [
                {
                    "created_at": "2015-08-01T00:00:00Z",
                    "description": "Charge <a href=\"/billing/cowork/receipt/\
1123\">1123</a> distribution for demo562-open-plus",
                    "amount": "112120",
                    "is_debit": false,
                    "orig_account": "Funds",
                    "orig_organization": "stripe",
                    "orig_amount": 112120,
                    "orig_unit": "usd",
                    "dest_account": "Funds",
                    "dest_organization": "cowork",
                    "dest_amount": 112120,
                    "dest_unit": "usd"
                }
            ]
        }
    """
    sort_fields_aliases = [('descr', 'description'),
                           ('dest_amount', 'amount'),
                           ('dest_organization__slug', 'dest_organization'),
                           ('dest_account', 'dest_account'),
                           ('orig_organization__slug', 'orig_organization'),
                           ('orig_account', 'orig_account'),
                           ('created_at', 'created_at')]

    natural_period = dateutil.relativedelta.relativedelta(days=-1)
    serializer_class = TransactionSerializer
    pagination_class = TotalPagination


class TransferQuerysetMixin(ProviderMixin):

    def get_queryset(self):
        """
        Get the list of transactions for this organization.
        """
        reconcile = not bool(self.request.GET.get('force', False))
        return self.organization.get_transfers(reconcile=reconcile)


class TransferListAPIView(SmartTransactionListMixin, TransferQuerysetMixin,
                          ListAPIView):
    """
    GET queries all ``Transaction`` associated to ``:organization`` while
    the organization acts as a provider in the relation.

    The queryset can be further filtered to a range of dates between
    ``start_at`` and ``ends_at``.

    The queryset can be further filtered by passing a ``q`` parameter.
    The value in ``q`` will be matched against:

      - Transaction.descr
      - Transaction.orig_organization.full_name
      - Transaction.dest_organization.full_name

    The result queryset can be ordered by:

      - Transaction.created_at
      - Transaction.descr
      - Transaction.dest_amount

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/cowork/transfers?start_at=2015-07-05T07:00:00.000Z\
&o=date&ot=desc

    **Example response**:

    .. sourcecode:: http

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "created_at": "2015-08-01T00:00:00Z",
                    "description": "Charge <a href=\"/billing/cowork/receipt/\
1123\">1123</a> distribution for demo562-open-plus",
                    "amount": "$1121.20",
                    "is_debit": false,
                    "orig_account": "Funds",
                    "orig_organization": "stripe",
                    "orig_amount": 112120,
                    "orig_unit": "usd",
                    "dest_account": "Funds",
                    "dest_organization": "cowork",
                    "dest_amount": 112120,
                    "dest_unit": "usd"
                }
            ]
        }
    """
    serializer_class = TransactionSerializer

    def list(self, request, *args, **kwargs):
        try:
            return super(TransferListAPIView, self).list(
                request, *args, **kwargs)
        except ProcessorError as err:
            raise ValidationError({'detail': "The latest transfers might"\
                " not be shown because there was an error with the backend"\
                " processor (ie. %s)." % str(err)})


class StatementBalanceAPIView(OrganizationMixin, APIView):
    """
    Get the statement balance due for an organization.

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/cowork/balance/

    **Example response**:

    .. sourcecode:: http

        {
            "balance_amount": "1200",
            "balance_unit": "usd"
        }
    """

    def get(self, request, *args, **kwargs):
        balance_amount, balance_unit \
            = Transaction.objects.get_statement_balance(self.organization)
        return Response({'balance_amount': balance_amount,
                         'balance_unit': balance_unit})


class CancelStatementBalanceAPIView(OrganizationMixin, DestroyAPIView):
    """
    Cancel the balance for a provider organization. This will create
    a transaction for this balance cancellation. A manager can use
    this endpoint to cancel balance dues that is known impossible
    to be recovered (e.g. an external bank or credit card company
    act).

    The endpoint returns the transaction created to cancel the
    balance due.

    **Example request**:

    .. sourcecode:: http

        DELETE /api/billing/cowork/balance/
    """

    def destroy(self, request, *args, **kwargs): #pylint:disable=unused-argument
        self.organization.create_cancel_transactions(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
