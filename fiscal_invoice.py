#!/usr/bin/python
# -*- coding: utf-8 -*-
# This program is free software; you can redistribute it and/or modify
# it under the terms of the Affero GNU General Public License as published by
# the Software Foundation; either version 3, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTIBILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# Copyright 2013 by Mariano Reingart
# Based on code "openerp_pyafipws" 

"Fiscal Invoice for Argentina's Federal Tax Administration (AFIP) homologated printers"

__author__ = "Mariano Reingart (reingart@gmail.com)"
__copyright__ = "Copyright (C) 2013 Mariano Reingart and others"
__license__ = "AGPL 3.0+"


from osv import fields, osv
import os, time
import datetime
import decimal
import os
import socket
import sys
import traceback

DEBUG = True


class fiscal_invoice(osv.osv):
    _name = "account.invoice"
    _inherit = "account.invoice"
    _order = "id"
    _columns = {}
    _defaults = {}

    def action_pyfiscalprinter_print(self, cr, uid, ids, context=None, *args):
        "Print the invoice in an AFIP homologated printer"
        for invoice in self.browse(cr, uid, ids):
            # if already printed, ignore
            if invoice.state != 'draft':
                continue
            # get the fiscal invoice type, point of sale and service:
            journal = invoice.journal_id
            company = journal.company_id
            ##tipo_cbte = journal.pyafipws_invoice_type
            ##punto_vta = journal.pyafipws_point_of_sale
            ##service = journal.pyafipws_electronic_invoice_service
            # check if it is an electronic invoice sale point:
            ##if not tipo_cbte or not punto_vta or not service:
            ##    continue

            # create the proxy and get the configuration system parameters:
            cfg = self.pool.get('ir.config_parameter')
            driver = cfg.get_param(cr, uid, 'pyfiscalprinter.driver', context=context) or "hasar"
            model = cfg.get_param(cr, uid, 'pyfiscalprinter.model', context=context) or "715"
            port = cfg.get_param(cr, uid, 'pyfiscalprinter.port', context=context) or "/dev/ttyS0"
            dummy = cfg.get_param(cr, uid, 'pyfiscalprinter.dummy', context=context) != "false"
            
            # import the AFIP printer helper for fiscal invoice
            if driver == 'epson':
                from pyfiscalprinter.epsonFiscal import EpsonPrinter
                printer = EpsonPrinter(deviceFile=port, model=model, dummy=dummy)
            elif driver == 'hasar':
                from pyfiscalprinter.hasarPrinter import HasarPrinter
                printer = HasarPrinter(deviceFile=port, model=model, dummy=dummy)
            else:
                raise osv.except_osv('Error !', "Unknown pyfiscalprinter driver: %s" % driver)

            # get the last 8 digit of the invoice number
            cbte_nro = int(invoice.number[-8:])
            # get the last invoice number registered in AFIP printer
            cbte_nro_afip = printer.getLastNumber("B")
            cbte_nro_next = int(cbte_nro_afip or 0) + 1
            # verify that the invoice is the next one to be registered in AFIP    
            if False and cbte_nro != cbte_nro_next:
                raise osv.except_osv('Error !', 
                        'Referencia: %s \n' 
                        'El número del comprobante debería ser %s y no %s' % (
                        str(invoice.number), str(cbte_nro_next), str(cbte_nro)))

            # customer tax number:
            if invoice.partner_id.vat:
                nro_doc = invoice.partner_id.vat.replace("-","")
            else:
                nro_doc = "0"               # only "consumidor final"
            tipo_doc = None
            if nro_doc.startswith("AR"):
                nro_doc = nro_doc[2:]
                if int(nro_doc)  == 0:
                    tipo_doc = 99           # consumidor final
                elif len(nro_doc) < 11:
                    tipo_doc = 96           # DNI
                else:
                    tipo_doc = 80           # CUIT

            # invoice amount totals:
            imp_total = abs(invoice.amount_total)
            imp_tot_conc = "0.00"
            imp_neto = abs(invoice.amount_untaxed)
            imp_iva = abs(invoice.amount_tax)
            imp_subtotal = imp_neto  # TODO: not allways the case!
            imp_trib = "0.00"
            imp_op_ex = "0.00"

            obs_generales = invoice.comment
            if invoice.payment_term:
                forma_pago = invoice.payment_term.name
                obs_comerciales = invoice.payment_term.name
            else:
                forma_pago = "Efectivo"
                obs_comerciales = None

            # customer data (foreign trade):
            nombre_cliente = invoice.partner_id.name
            if invoice.partner_id.vat:
                if invoice.partner_id.vat.startswith("AR"):
                    # use the Argentina AFIP's global CUIT for the country:
                    cuit_pais_cliente = invoice.partner_id.vat[2:]
                    id_impositivo = None
                else:
                    # use the VAT number directly
                    id_impositivo = invoice.partner_id.vat[2:] 
                    # TODO: the prefix could be used to map the customer country
                    cuit_pais_cliente = None
            else:
                cuit_pais_cliente = id_impositivo = None
            if invoice.address_invoice_id:
                domicilio_cliente = " - ".join([
                                    invoice.address_invoice_id.name or '',
                                    invoice.address_invoice_id.street or '',
                                    invoice.address_invoice_id.street2 or '',
                                    invoice.address_invoice_id.zip or '',
                                    invoice.address_invoice_id.city or '',
                                    ])
            else:
                domicilio_cliente = ""

            # start to print the invoice:
            printer.cancelAnyDocument()
            printer.openTicket()
            printer.printNonFiscalText("generado desde openerp!")

            # print line items - invoice detail
            for line in invoice.invoice_line:
                codigo = line.product_id.code
                u_mtx = 1                       # TODO: get it from uom? 
                cod_mtx = line.product_id.ean13
                ds = line.name
                qty = line.quantity
                umed = 7                        # TODO: line.uos_id...?
                price = line.price_unit
                importe = line.price_subtotal
                discount = line.discount or 0
                iva = 21 # line.invoice_line_tax_id[0].amount    # VAT
                printer.addItem(ds, qty, price, iva, discount, discountDescription="")

            
            # Send the payment terms           
            printer.addPayment(forma_pago, imp_total)

            # Send additional data
            if obs_generales:
                printer.printNonFiscalText(obs_generales)
            if obs_comerciales:
                printer.printNonFiscalText(obs_comerciales)

            # close the invoice
            ret = printer.closeDocument()        
            self.log(cr, uid, invoice.id, str(ret))


fiscal_invoice()
