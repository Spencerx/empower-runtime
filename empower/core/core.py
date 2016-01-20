#!/usr/bin/env python3
#
# Copyright (c) 2016, Roberto Riggio
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#    * Neither the name of the CREATE-NET nor the
#      names of its contributors may be used to endorse or promote products
#      derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY CREATE-NET ''AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL CREATE-NET BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""EmPOWER Runtime."""

from sqlalchemy.exc import IntegrityError

from empower.persistence import Session
from empower.persistence.persistence import TblTenant
from empower.persistence.persistence import TblAccount
from empower.persistence.persistence import TblPendingTenant
from empower.core.account import Account
from empower.core.tenant import Tenant

import empower.logger
LOG = empower.logger.get_logger()

DEFAULT_PERIOD = 5000


def generate_default_accounts():
    """Generate default accounts.

    Three default accounts (one root account and two user accounts are created
    the first time the controller is started.
    """

    if not Session().query(TblAccount).all():

        LOG.info("Generating default accounts")

        session = Session()
        session.add(TblAccount(username="root",
                               password="root",
                               role="admin",
                               name="Administrator",
                               surname="",
                               email="admin@empower.net"))
        session.add(TblAccount(username="foo",
                               password="foo",
                               role="user",
                               name="Foo",
                               surname="",
                               email="foo@empower.net"))
        session.add(TblAccount(username="bar",
                               password="bar",
                               role="user",
                               name="Bar",
                               surname="",
                               email="bar@empower.net"))
        session.commit()


class EmpowerRuntime(object):
    """EmPOWER Runtime."""

    def __init__(self):

        self.components = {}
        self.accounts = {}
        self.tenants = {}
        self.lvaps = {}
        self.wtps = {}
        self.cpps = {}
        self.feeds = {}

        LOG.info("Starting EmPOWER Runtime")

        # generate default users if database is empty
        generate_default_accounts()

        LOG.info("Loading EmPOWER Runtime defaults")

        self.__load_accounts()
        self.__load_tenants()

    def __load_accounts(self):
        """Load accounts table."""

        for account in Session().query(TblAccount).all():

            self.accounts[account.username] = Account(account.username,
                                                      account.password,
                                                      account.name,
                                                      account.surname,
                                                      account.email,
                                                      account.role)

    def __load_tenants(self):
        """Load Tenants."""

        for tenant in Session().query(TblTenant).all():

            if tenant.tenant_id in self.tenants:
                raise KeyError(tenant.tenant_id)

            self.tenants[tenant.tenant_id] = \
                Tenant(tenant.tenant_id,
                       tenant.tenant_name,
                       tenant.owner,
                       tenant.desc)

    def create_account(self, username, password, role, name, surname, email):
        """Create a new account."""

        if username in self.accounts:
            LOG.error("'%s' already registered", username)
            raise ValueError("%s already registered" % username)

        session = Session()
        account = TblAccount(username=username,
                             password=password,
                             role=role,
                             name=name,
                             surname=surname,
                             email=email)

        session.add(account)
        session.commit()

        self.accounts[account.username] = Account(account.username,
                                                  account.password,
                                                  account.name,
                                                  account.surname,
                                                  account.email,
                                                  account.role)

    def remove_account(self, username):
        """Remove an account."""

        if username == 'root':
            raise ValueError("Cannot removed root account")

        account = Session().query(TblAccount) \
                           .filter(TblAccount.username == str(username)) \
                           .first()
        if not account:
            raise KeyError(username)

        session = Session()
        session.delete(account)
        session.commit()

        del self.accounts[username]
        to_be_deleted = [x.tenant_id for x in self.tenants.values()
                         if x.owner == username]

        for tenant_id in to_be_deleted:
            self.remove_tenant(tenant_id)

    def update_account(self, username, request):
        """Update an account."""

        account = self.accounts[username]

        for param in request:
            setattr(account, param, request[param])

    def register(self, name, init_method, params):
        """Register new component."""

        if name in self.components:
            LOG.error("'%s' already registered", name)
            raise ValueError("%s already registered" % name)

        LOG.info("Registering '%s'", name)
        self.components[name] = init_method(**params)
        self.components[name].path = name
        if hasattr(self.components[name], "start"):
            self.components[name].start()

    def unregister(self, name):
        """Unregister component."""

        LOG.info("Unregistering '%s'", name)

        worker = self.components[name]

        from empower.core.module import ModuleWorker
        from empower.core.app import EmpowerApp

        if not issubclass(type(worker), ModuleWorker) and \
           not issubclass(type(worker), EmpowerApp):

            raise ValueError("Module %s cannot be removed", name)

        # if this was a worker then remove all modules
        if issubclass(type(worker), ModuleWorker):

            to_be_removed = []

            for module in self.components[name].modules.values():
                to_be_removed.append(module.module_id)

            for remove in to_be_removed:
                self.components[name].remove_module(remove)

        self.components[name].remove_handlers()
        del self.components[name]

    def get_account(self, username):
        """Load user credential from the username."""

        if username not in self.accounts:
            return None

        return self.accounts[username]

    def check_permission(self, username, password):
        """Check if username/password match."""

        if username not in self.accounts:
            return False

        if self.accounts[username].password != password:
            return False

        return True

    def add_tenant(self, owner, desc, tenant_name, tenant_id=None):
        """Create new Tenant."""

        if tenant_id in self.tenants:
            raise ValueError("Tenant %s exists", tenant_id)

        try:

            session = Session()

            if tenant_id:
                request = TblTenant(tenant_id=tenant_id,
                                    tenant_name=tenant_name,
                                    owner=owner,
                                    desc=desc)
            else:
                request = TblTenant(owner=owner,
                                    tenant_name=tenant_name,
                                    desc=desc)

            session.add(request)
            session.commit()

        except IntegrityError:
            session.rollback()
            raise ValueError("Tenant name %s exists", tenant_name)

        self.tenants[request.tenant_id] = \
            Tenant(request.tenant_id,
                   request.tenant_name,
                   self.accounts[owner].username,
                   desc)

        return request.tenant_id

    @classmethod
    def load_pending_tenant(cls, tenant_id):
        """Load pending tenant request."""

        return Session().query(TblPendingTenant) \
                        .filter(TblPendingTenant.tenant_id == tenant_id) \
                        .first()

    @classmethod
    def load_pending_tenants(cls, username=None):
        """Fetch pending tenants requests."""

        if username:
            return Session().query(TblPendingTenant) \
                            .filter(TblPendingTenant.owner == username) \
                            .all()
        else:
            return Session().query(TblPendingTenant).all()

    def request_tenant(self, owner, desc, tenant_name, tenant_id=None):
        """Request new Tenant."""

        if tenant_id in self.tenants:
            raise ValueError("Tenant %s exists", tenant_id)

        if self.load_pending_tenant(tenant_id):
            raise ValueError("Tenant %s exists", tenant_id)

        try:

            session = Session()

            if tenant_id:
                request = TblPendingTenant(tenant_id=tenant_id,
                                           owner=owner,
                                           tenant_name=tenant_name,
                                           desc=desc)
            else:
                request = TblPendingTenant(owner=owner,
                                           tenant_name=tenant_name,
                                           desc=desc)

            session.add(request)
            session.commit()

        except IntegrityError:
            session.rollback()
            raise ValueError("Tenant name %s exists", tenant_name)

        return request.tenant_id

    @classmethod
    def reject_tenant(cls, tenant_id):
        """Reject previously requested Tenant."""

        pending = Session().query(TblPendingTenant) \
            .filter(TblPendingTenant.tenant_id == tenant_id) \
            .first()

        if not pending:
            raise KeyError(tenant_id)

        session = Session()
        session.delete(pending)
        session.commit()

    def remove_tenant(self, tenant_id):
        """Delete existing Tenant."""

        if tenant_id not in self.tenants:
            raise KeyError(tenant_id)

        # remove tenant
        del self.tenants[tenant_id]

        tenant = Session().query(TblTenant) \
                          .filter(TblTenant.tenant_id == tenant_id) \
                          .first()

        session = Session()
        session.delete(tenant)
        session.commit()

        # remove running modules
        for component in self.components.values():

            if not hasattr(component, 'modules'):
                continue

            to_be_removed = []

            for module in component.modules.values():
                if module.tenant_id == tenant_id:
                    to_be_removed.append(module.module_id)

            for module_id in to_be_removed:
                component.remove_module(module_id)
