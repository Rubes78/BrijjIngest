#!/bin/bash
sudo systemctl start brijjdata
echo "BrijjData Config UI started."
echo "Access it at http://$(hostname -I | awk '{print $1}'):5000"
